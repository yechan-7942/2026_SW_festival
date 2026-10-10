"""발표자료 완성본(PPTX)을 현재 산출물에서 만든다 — reports/presentation_revision_draft.md의 슬라이드 수정안 구현.

원본 PPTX가 없어 기존 PDF의 구현과 어긋난 슬라이드(3~9)는 실제 결과로 새로 구성하고,
그대로 쓰는 표지·문제 정의·Q&A도 편집할 수 있게 도형으로 다시 그린다(사진만 원본 PDF에서 잘라 씀).
수치는 전부 data/processed/*.parquet에서 읽는다(손으로 옮겨 적지 않는다). 지도 그림도 여기서 다시 그린다.
아이콘은 assets/slide_icons/(react-icons Font Awesome을 PNG로 렌더링한 것)를 쓴다.
실행: uv run --with python-pptx --with matplotlib python scripts/build_presentation.py
"""

import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager, patheffects  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.access.network import compare_road_vs_straight  # noqa: E402
from src.gap.validation import access_rank_of, compare_access_indicators, road_gap_comparison, weight_sweep  # noqa: E402

OUT = ROOT / "outputs/presentation_final.pptx"
FIG = ROOT / "outputs/figures/slides"
ICONS = ROOT / "assets/slide_icons"
FONT = "Apple SD Gothic Neo"
W, H = 13.333, 7.5

NAVY = RGBColor(0x0F, 0x17, 0x2A)
NAVY_2 = RGBColor(0x1E, 0x29, 0x3B)
INK = RGBColor(0x1E, 0x29, 0x3B)
SLATE = RGBColor(0x47, 0x55, 0x69)
MUTED = RGBColor(0x94, 0xA3, 0xB8)
TEAL = RGBColor(0x0F, 0x96, 0x88)
TEAL_BRIGHT = RGBColor(0x2D, 0xD4, 0xBF)
TEAL_SOFT = RGBColor(0xE6, 0xF4, 0xF1)
CORAL = RGBColor(0xE8, 0x61, 0x3C)
CORAL_SOFT = RGBColor(0xFD, 0xED, 0xE7)
SOFT = RGBColor(0xF1, 0xF5, 0xF9)
LINE = RGBColor(0xE2, 0xE8, 0xF0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


# ---------- 그리기 도우미 ----------

def rect(s, x, y, w, h, fill, line=None, rounded=True, radius=0.06):
    shape = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    r = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        r.adjustments[0] = radius
    r.fill.solid()
    r.fill.fore_color.rgb = fill
    if line is None:
        r.line.fill.background()
    else:
        r.line.color.rgb = line
        r.line.width = Pt(1)
    r.shadow.inherit = False
    return r


def oval(s, x, y, d, fill):
    r = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    r.fill.solid()
    r.fill.fore_color.rgb = fill
    r.line.fill.background()
    r.shadow.inherit = False
    return r


def txt(s, x, y, w, h, paras, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT, after=6, line=None):
    """paras: 문단 목록. 문단 하나는 (글, 크기, 굵게, 색) 또는 그런 튜플들의 리스트(한 문단 안의 여러 run)."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(after)
        if line:
            p.line_spacing = line
        for t, size, bold, color in para if isinstance(para, list) else [para]:
            r = p.add_run()
            r.text = t
            r.font.name, r.font.size, r.font.bold, r.font.color.rgb = FONT, Pt(size), bold, color
    return tb


def icon(s, name, color, x, y, size):
    s.shapes.add_picture(str(ICONS / f"{name}_{color}.png"), Inches(x), Inches(y), Inches(size), Inches(size))


def icon_badge(s, name, x, y, d=0.62, fill=TEAL, color="white"):
    oval(s, x, y, d, fill)
    pad = d * 0.27
    icon(s, name, color, x + pad, y + pad, d - 2 * pad)


def number_badge(s, n, x, y, d, fill, size=16):
    oval(s, x, y, d, fill)
    txt(s, x, y, d, d, [(str(n), size, True, WHITE)], anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, after=0)


def blank(prs, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    if dark:
        rect(s, 0, 0, W, H, NAVY, rounded=False)
    return s


def content(prs, kicker, title, page):
    s = blank(prs)
    txt(s, 0.7, 0.5, 8, 0.3, [(kicker, 12, True, TEAL)], after=0)
    txt(s, 0.7, 0.8, 11.9, 0.7, [(title, 30, True, NAVY)], after=0)
    txt(s, W - 1.2, H - 0.5, 0.6, 0.3, [(str(page), 10, False, MUTED)], align=PP_ALIGN.RIGHT, after=0)
    return s


# ---------- 지도 ----------

def _hex(c):
    return "#" + str(c)


def _blend(a, b, t):
    a, b = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def draw_maps(df, stable):
    """표지용(어두운 배경) 지도와 결과용 지도. 상위 동은 강조색으로 칠한다."""
    FIG.mkdir(parents=True, exist_ok=True)
    gdf = gpd.read_parquet(ROOT / "data/processed/admin_units.parquet")[["adm_cd", "geometry"]]
    gdf = gdf.merge(df[["adm_cd", "adm_nm", "gap_score"]], on="adm_cd")
    t = (gdf["gap_score"] - gdf["gap_score"].min()) / (gdf["gap_score"].max() - gdf["gap_score"].min())
    hot = gdf["adm_nm"].isin(stable)
    font = font_manager.FontProperties(fname="/System/Library/Fonts/AppleSDGothicNeo.ttc")

    fig, ax = plt.subplots(figsize=(6, 7))
    colors = [_hex("2DD4BF") if h else _blend("#1E293B", "#475569", v) for h, v in zip(hot, t)]
    gdf.plot(ax=ax, color=colors, edgecolor="#0F172A", linewidth=0.8)
    ax.set_axis_off()
    fig.savefig(FIG / "map_cover.png", dpi=220, transparent=True, bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 7))
    colors = [_hex("E8613C") if h else _blend("#E6F4F1", "#0F9688", v) for h, v in zip(hot, t)]
    gdf.plot(ax=ax, color=colors, edgecolor="white", linewidth=0.9)
    for geom, name in zip(gdf[hot].geometry, gdf[hot]["adm_nm"]):
        p = geom.representative_point()
        ax.text(p.x, p.y, name, fontproperties=font, fontsize=13, ha="center", va="center", color="#0F172A",
                path_effects=[patheffects.withStroke(linewidth=3.5, foreground="white")])
    ax.set_axis_off()
    fig.savefig(FIG / "map_result.png", dpi=220, transparent=True, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def add_fit(s, path, x, y, w, h):
    """비율을 지키며 (x, y, w, h) 안에 가운데 맞춰 넣는다."""
    from PIL import Image
    iw, ih = Image.open(path).size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    return s.shapes.add_picture(str(path), Inches(x + (w - pw) / 2), Inches(y + (h - ph) / 2), Inches(pw), Inches(ph))


def add_cover(s, path, x, y, w, h):
    """비율을 지키며 (x, y, w, h)를 꽉 채우고 넘치는 쪽을 잘라 낸다."""
    from PIL import Image
    iw, ih = Image.open(path).size
    pic = s.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
    box_r, img_r = w / h, iw / ih
    if img_r > box_r:
        cut = (1 - box_r / img_r) / 2
        pic.crop_left = pic.crop_right = cut
    else:
        cut = (1 - img_r / box_r) / 2
        pic.crop_top = pic.crop_bottom = cut
    return pic


# ---------- 본문 ----------

def main():
    gap = pd.read_parquet(ROOT / "data/processed/gap_scores.parquet")
    units = pd.read_parquet(ROOT / "data/processed/admin_units.parquet")[["adm_cd", "adm_nm", "pop_total", "pop_foreign"]]
    rob = pd.read_parquet(ROOT / "data/processed/gap_robustness.parquet")
    cards = pd.read_parquet(ROOT / "data/processed/policy_cards.parquet")
    df = gap.merge(units, on="adm_cd").merge(rob[["adm_cd", "gap_type", "rank_spread", "top_in_all"]], on="adm_cd").sort_values("rank")
    stable = df[df["top_in_all"]]["adm_nm"].tolist()
    n_stable = len(stable)
    # 접근성 지표를 바꿔도(E2SFCA 등) 같은 동이 상위에 남는지 — reports/m6_validation.md §2
    alt = compare_access_indicators()
    n_alt_agree = sum(set(stable) <= set(top) for top in alt["top_n"])
    alt_rho = alt[alt["indicator"] != "2SFCA"]["spearman_vs_2sfca"]
    # 가중치: 이 값 이상이면 모든 임계거리에서 stable 전부가 5위 안 — §1
    sweep = weight_sweep()
    nm = units.set_index("adm_cd")["adm_nm"]
    worst = sweep[sweep["adm_cd"].map(nm).isin(stable)].groupby("demand_weight")["rank"].max()
    ok = worst <= 5
    min_ok_weight = next(w for w in sorted(worst.index) if ok.loc[w:].all())
    # 도로 거리 — §3
    road = compare_road_vs_straight()
    road_cmp = road_gap_comparison(road)
    n_road_agree = sum(set(stable) <= set(top) for top in road_cmp["road_top_n"])
    detour = road["detour_quantiles"]["p50"]
    # 접근성만의 순위(1 = 가장 나쁨) — "병원이 가장 부족한 곳"이 아님을 보여 준다
    access_ranks = access_rank_of(stable)
    n_dong = len(df)
    jumpy = df.sort_values("rank_spread", ascending=False).iloc[0]

    draw_maps(df, stable)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)

    # 1. 표지 (원본 1)
    s = blank(prs, dark=True)
    add_fit(s, FIG / "map_cover.png", 7.4, 0.5, 5.4, 6.5)
    pill = rect(s, 0.8, 1.9, 4.3, 0.46, RGBColor(0x13, 0x3B, 0x40), line=RGBColor(0x1F, 0x9E, 0x8E), radius=0.5)
    pill.text_frame.text = ""
    txt(s, 0.8, 1.9, 4.3, 0.46, [("공공데이터 & AI 융합 의사결정 지원 시스템", 13, False, TEAL_BRIGHT)],
        anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, after=0)
    txt(s, 0.8, 2.65, 6.8, 1.9, [("포항시 외국인 주민 생활 인프라", 38, True, WHITE), ("격차 진단·정책 후보 생성 시스템", 38, True, WHITE)],
        after=4, line=1.05)
    txt(s, 0.8, 4.65, 6.4, 0.8, [("공간 데이터 2SFCA 분석과 LLM 기반 근거 중심 정책 리포팅", 16, False, MUTED)], after=0)
    oval(s, 0.8, 6.27, 0.16, TEAL_BRIGHT)
    txt(s, 1.08, 6.18, 5.5, 0.34, [(f"민트색: 기준을 바꿔도 격차 상위 5위에 남는 {n_stable}개 읍·면", 12, False, MUTED)],
        anchor=MSO_ANCHOR.MIDDLE, after=0)

    # 2. 문제 정의 (원본 2)
    s = content(prs, "왜 이 문제인가", "정주 여건의 사각지대", 2)
    add_cover(s, ROOT / "outputs/figures/problem_photo.jpg", 8.0, 0, W - 8.0, H)
    problems = [
        ("industry", "외국인 주민의 지속적 유입", "포스코·영일만 산업단지를 중심으로 외국인 노동자와 다문화 가구가 계속 늘고 있습니다."),
        ("search", "체계적 분석 부재", "핵심 산업 인력인데도 의료·금융·행정 같은 기초 생활 인프라의 격차 진단이 없었습니다."),
        ("language", "언어·정보 장벽의 이중고", "같은 인프라 결핍에서도 외국인은 내국인보다 더 큰 불이익을 겪습니다."),
        ("signout", "지역 이탈 위기", "정주 여건이 나빠지면 인구 유출과 산업 노동력 손실로 이어집니다."),
    ]
    for i, (ic, head, body) in enumerate(problems):
        y = 1.95 + i * 1.28
        icon_badge(s, ic, 0.7, y, 0.6)
        txt(s, 1.55, y - 0.02, 5.9, 1.1, [(head, 18, True, NAVY), (body, 14, False, SLATE)], after=3)

    # 3. 접근 방법
    s = content(prs, "무엇을 했나", "어디부터, 무엇을 할지까지", 3)
    steps = [
        ("database", "공공데이터 수집", "인구·외국인·병원·약국\n위치와 규모"),
        ("map", "격차 점수", f"{n_dong}개 행정동마다\n접근성 × 외국인 비율"),
        ("robot", "LLM 정책 카드", "동마다 정책 초안 작성\n틀린 출력은 자동 재작성"),
        ("usercheck", "사람 검수", "정책 타당성은\nGM이 최종 검토"),
    ]
    xs = [0.7 + i * 3.08 for i in range(4)]
    for i, ((ic, head, body), x) in enumerate(zip(steps, xs)):
        last = i == len(steps) - 1
        rect(s, x, 1.95, 2.72, 3.1, TEAL_SOFT if not last else SOFT)
        icon_badge(s, ic, x + 0.3, 2.25, 0.78, fill=TEAL if not last else SLATE)
        txt(s, x + 0.3, 3.25, 2.2, 0.3, [(f"STEP {i + 1}", 11, True, TEAL if not last else SLATE)], after=0)
        txt(s, x + 0.3, 3.55, 2.2, 1.4, [(head, 20, True, NAVY), (body, 14, False, SLATE)], after=6)
        if not last:
            icon(s, "arrow", "gray", x + 2.79, 3.35, 0.26)
    rect(s, 0.7, 5.45, 11.93, 1.05, NAVY)
    txt(s, 1.05, 5.45, 11.3, 1.05, [[("점수로 먼저 볼 동을 고르고, ", 18, False, WHITE), ("그 동에 맞는 정책 초안", 18, True, TEAL_BRIGHT),
                                     ("까지 만듭니다", 18, False, WHITE)]], anchor=MSO_ANCHOR.MIDDLE, after=0)

    # 4. 데이터
    s = content(prs, "데이터", "모두 공개 데이터로", 4)
    sources = [
        ("users", "외국인·인구", "KOSIS 포항시 지역통계(2025) · SGIS 행정동 경계와 집계구 인구 1,046개"),
        ("hospital", "병원·약국", "심평원 의료기관 908곳 · 병원은 의사 수로 규모 반영"),
        ("book", "배경 조사", "경북 외국인주민 실태조사(2023) · 전국다문화가족실태조사(2025) 공개 결과"),
    ]
    for i, (ic, head, body) in enumerate(sources):
        y = 1.95 + i * 1.5
        icon_badge(s, ic, 0.7, y, 0.7)
        txt(s, 1.65, y - 0.02, 5.6, 1.3, [(head, 19, True, NAVY), (body, 14, False, SLATE)], after=4)
    rect(s, 7.75, 1.95, 4.88, 4.55, NAVY)
    icon(s, "warn", "coral", 8.15, 2.3, 0.42)
    txt(s, 8.15, 2.9, 4.2, 0.5, [("구하지 못한 데이터", 20, True, WHITE)], after=0)
    txt(s, 8.15, 3.55, 4.15, 2.8, [
        ("금융", 15, True, TEAL_BRIGHT),
        ("상가정보 API에 포항 금융업 0건 → 의료만 분석", 14, False, WHITE),
        ("다문화가족실태조사 원자료", 15, True, TEAL_BRIGHT),
        ("유료 원격접근만 가능 → 공개 보고서로 대체", 14, False, WHITE),
        ("그래서 동마다 다른 체감 결핍은 반영하지 못했습니다", 12, False, MUTED),
    ], after=5)

    # 5. 격차 점수
    s = content(prs, "핵심 알고리즘", "격차 점수", 5)
    rect(s, 0.7, 1.9, 11.93, 1.8, SOFT)
    txt(s, 0.7, 2.05, 11.93, 1.0, [[("격차 점수  =  0.5 × ", 30, True, NAVY), ("외국인 비율", 30, True, TEAL),
                                   ("  +  0.5 × ", 30, True, NAVY), ("(1 − 의료 접근성)", 30, True, CORAL)]],
        anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, after=0)
    txt(s, 0.7, 3.05, 11.93, 0.4, [("두 값 모두 0~1로 정규화 · 0에 가까울수록 여건이 좋은 동", 13, False, SLATE)],
        align=PP_ALIGN.CENTER, after=0)
    terms = [
        ("users", TEAL, "외국인 비율", "외국인 주민 수 ÷ 동 인구"),
        ("hospital", CORAL, "의료 접근성 (2SFCA)", "주민 1명이 이용할 수 있는 의사 수를 거리 3km 안에서 계산 · 1km·5km로도 비교"),
        ("scale", SLATE, "가중치 0.5 : 0.5", "검토 전 중립 기본값 · 민감도 분석으로 함께 확인"),
    ]
    for i, (ic, color, head, body) in enumerate(terms):
        x = 0.7 + i * 4.06
        rect(s, x, 4.05, 3.81, 2.45, WHITE, line=LINE)
        icon_badge(s, ic, x + 0.35, 4.35, 0.62, fill=color)
        txt(s, x + 0.35, 5.15, 3.15, 1.3, [(head, 18, True, NAVY), (body, 14, False, SLATE)], after=4)

    # 6. 정책 카드 검사
    s = content(prs, "LLM 가드레일", "정책 카드는 검사를 통과해야 나갑니다", 6)
    flow = [("점수·동 정보·조사 수치", TEAL_SOFT, NAVY), ("LLM 초안", TEAL_SOFT, NAVY), ("자동 검사", TEAL, WHITE), ("GM 검토 예정", SOFT, NAVY)]
    for i, (label, fill, color) in enumerate(flow):
        w = 2.1 if i == 0 else 1.4
        x = 0.7 if i == 0 else 0.7 + 2.1 + 0.35 + (i - 1) * 1.75
        rect(s, x, 1.95, w, 0.62, fill, radius=0.5)
        txt(s, x, 1.95, w, 0.62, [(label, 13, True, color)], anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, after=0)
        if i < len(flow) - 1:
            icon(s, "arrow", "gray", x + w + 0.09, 2.17, 0.18)
    txt(s, 0.7, 2.95, 7, 0.4, [("이런 출력은 다시 쓰게 합니다", 17, True, NAVY)], after=0)
    rejects = ["입력에 없는 숫자", "한자·외국어가 섞인 문장", "중간에 잘린 응답", "추론 과정이 새어 나온 글", "위법 제안 (처방전 없이 약 제공)"]
    for i, r in enumerate(rejects):
        col, row = divmod(i, 3)
        x, y = 0.7 + col * 3.67, 3.5 + row * 0.86
        rect(s, x, y, 3.5, 0.7, SOFT)
        icon(s, "times", "coral", x + 0.25, y + 0.23, 0.24)
        txt(s, x + 0.65, y, 2.8, 0.7, [(r, 15, False, INK)], anchor=MSO_ANCHOR.MIDDLE, after=0)
    rect(s, 4.37, 5.22, 3.5, 0.7, TEAL_SOFT)
    icon(s, "check", "teal", 4.62, 5.45, 0.24)
    txt(s, 5.02, 5.22, 2.8, 0.7, [("통과한 카드만 GM에게", 15, True, TEAL)], anchor=MSO_ANCHOR.MIDDLE, after=0)
    txt(s, 0.7, 6.2, 7, 0.4, [("코드는 형식·숫자만 확인하고, 정책이 타당한지는 사람이 봅니다", 13, False, SLATE)], after=0)
    rect(s, 8.45, 1.95, 4.18, 4.55, NAVY)
    txt(s, 8.85, 2.3, 3.5, 0.4, [("검사 없이 돌렸을 때", 15, True, MUTED)], after=0)
    txt(s, 8.85, 2.75, 3.5, 1.2, [[(f"{n_dong}", 66, True, CORAL), ("건", 26, True, CORAL)]], after=0)
    txt(s, 8.85, 4.1, 3.5, 2.3, [("거의 같은 처방으로 수렴", 19, True, WHITE),
                                ("동마다 사정이 다른데도 비슷한 정책이 나왔고, 약사법에 어긋나는 제안도 섞였습니다. 그래서 위 검사를 만들었습니다.", 15, False, MUTED)], after=8, line=1.15)

    # 7. 결과
    s = content(prs, "분석 결과", f"남구 외곽 읍·면 {n_stable}곳이 먼저 볼 곳", 7)
    add_fit(s, FIG / "map_result.png", 0.5, 1.6, 5.9, 5.6)
    txt(s, 6.9, 1.95, 5.7, 0.5, [[("1·3·5km 어느 기준에서도 ", 16, False, SLATE), ("격차 상위 5위", 16, True, CORAL)]], after=0)
    for i, r in enumerate(df[df["top_in_all"]].itertuples()):
        y = 2.6 + i * 0.86
        rect(s, 6.9, y, 5.73, 0.72, CORAL_SOFT if i == 0 else SOFT)
        number_badge(s, r.rank, 7.08, y + 0.14, 0.44, CORAL, 15)
        txt(s, 7.75, y, 2.2, 0.72, [(r.adm_nm, 18, True, NAVY)], anchor=MSO_ANCHOR.MIDDLE, after=0)
        txt(s, 9.6, y, 2.85, 0.72, [[("격차 ", 12, False, SLATE), (f"{r.gap_score:.2f}", 16, True, NAVY),
                                      ("   외국인 ", 12, False, SLATE), (f"{r.pop_foreign / r.pop_total:.1%}", 16, True, NAVY)]],
            anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT, after=0)
    a = access_ranks["2SFCA"]
    txt(s, 6.9, 6.15, 5.73, 0.8, [
        (f"포항에서 외국인 비율이 가장 높은 {n_stable}곳 · 의료 접근성은 {n_dong}개 동 중 {a.min()}~{a.max()}번째로 나쁨", 12, False, SLATE),
        (f"{jumpy.adm_nm}처럼 기준에 따라 {int(jumpy.rank_spread)}계단 움직이는 동은 순위를 단정하지 않음", 12, False, MUTED),
    ], after=3)

    # 8. 검증 — reports/m6_validation.md
    s = content(prs, "검증", "방법을 바꿔도 같은 곳이 나오는가", 8)
    checks = [
        ("sliders", "가중치를 바꿔도", f"{min_ok_weight:.1f}", "이상",
         f"외국인 비율 가중치를 0~1로 바꿔 봄. 이 값 이상이면 1·3·5km 모두 {n_stable}곳이 5위 안"),
        ("layer", "접근성 지표를 바꿔도", f"{n_alt_agree - 1}/{len(alt_rho)}", "지표",
         f"E2SFCA·반경 내 의사 수·최근접 거리·동 내 의사 수에서도 {n_stable}곳 유지 (순위상관 {alt_rho.min():.2f}~{alt_rho.max():.2f})"),
        ("route", "도로 거리로 재도", f"{road_cmp['spearman'].min():.2f}", "순위상관",
         f"실제 도로는 직선의 약 {detour:.2f}배. 도로 거리로 다시 계산해도 {n_road_agree}/{len(road_cmp)}개 기준에서 {n_stable}곳 유지"),
    ]
    for i, (ic, head, big, unit, body) in enumerate(checks):
        x = 0.7 + i * 4.06
        rect(s, x, 1.95, 3.81, 3.15, SOFT)
        icon(s, ic, "teal", x + 0.35, 2.3, 0.4)
        txt(s, x + 0.95, 2.3, 2.7, 0.4, [(head, 15, True, SLATE)], anchor=MSO_ANCHOR.MIDDLE, after=0)
        txt(s, x + 0.35, 2.85, 3.2, 0.95, [[(big, 44, True, TEAL), (f" {unit}", 16, True, TEAL)]], after=0)
        txt(s, x + 0.35, 3.85, 3.15, 1.2, [(body, 13, False, SLATE)], after=0, line=1.1)
    rect(s, 0.7, 5.4, 11.93, 1.1, CORAL_SOFT)
    icon(s, "bulb", "coral", 1.0, 5.7, 0.48)
    txt(s, 1.75, 5.4, 10.6, 1.1, [
        [(f"{n_stable}곳이 남는 건 주로 높은 외국인 비율 때문", 16, True, NAVY)],
        [("접근성만 보면 ", 13, False, SLATE), (f"{access_ranks.min().min()}~{access_ranks.max().max()}위", 13, True, SLATE),
         ("로 흩어집니다. 그래서 '병원이 가장 부족한 곳'이 아니라 ", 13, False, SLATE), ("'먼저 살펴볼 곳'", 13, True, CORAL),
         ("으로 봅니다. 정답 데이터가 없어 정확도 자체는 재지 못했습니다.", 13, False, SLATE)],
    ], anchor=MSO_ANCHOR.MIDDLE, after=3)

    # 9. 다학제 융합 (원본 8 — NLP·RAG 서술은 실제 구현에 맞게 고침)
    s = content(prs, "팀", "다학제 융합", 9)
    roles = [
        ("code", "컴퓨터공학 심화", ["공공데이터 수집·정제 파이프라인", "공간데이터(GIS) 처리", "격차 지도·대시보드"]),
        ("robot", "AI 융합", ["2SFCA 접근성 계산", "LLM 정책 카드 생성", "출력 자동 검사·재작성"]),
        ("globe", "글로벌 매니지먼트", ["문제 정의", "가중치 검토 예정", "정책 타당성 검토 예정"]),
    ]
    for i, (ic, head, items) in enumerate(roles):
        x = 0.7 + i * 4.06
        rect(s, x, 1.95, 3.81, 3.85, SOFT)
        icon_badge(s, ic, x + 0.4, 2.35, 0.9)
        txt(s, x + 0.4, 3.55, 3.1, 0.5, [(head, 21, True, NAVY)], after=0)
        txt(s, x + 0.4, 4.2, 3.1, 1.5, [(f"· {t}", 15, False, SLATE) for t in items], after=7)
    txt(s, 0.7, 6.15, 11.93, 0.5, [[("코드가 낸 결과를 ", 16, False, SLATE), ("정책 전공이 검토할", 16, True, TEAL), (" 계획입니다 · 실제 검토 기록은 아직 필요합니다", 16, False, SLATE)]],
        align=PP_ALIGN.CENTER, after=0)

    # 10. 결과물
    s = content(prs, "결과물", "대시보드와 정책 카드", 10)
    rect(s, 0.7, 1.9, 7.1, 4.6, SOFT)
    add_fit(s, ROOT / "outputs/figures/dashboard_shot.png", 0.9, 2.1, 6.7, 4.2)
    first = cards.sort_values("rank").iloc[0]
    rect(s, 8.15, 1.9, 4.48, 4.6, WHITE, line=LINE)
    rect(s, 8.45, 2.2, 1.1, 0.36, CORAL_SOFT, radius=0.5)
    txt(s, 8.45, 2.2, 1.1, 0.36, [("격차 1위", 11, True, CORAL)], anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, after=0)
    txt(s, 8.45, 2.7, 3.9, 0.5, [(f"{first.adm_nm} 정책 카드", 19, True, NAVY)], after=0)
    body = [l.strip() for l in first.policy_text.splitlines() if l.strip()]
    txt(s, 8.45, 3.3, 3.9, 3.1, [(l, 12, False, SLATE) for l in body], after=6, line=1.1)

    # 11. 기대 효과와 한계
    s = content(prs, "정리", "기대 효과와 한계", 11)
    goods = ["어느 동부터 볼지 데이터로 우선순위를 정함", f"상위 {n_stable}곳은 기준을 바꿔도 유지 → 예산 우선 후보",
             "공개 데이터만 사용 → 새 통계로 매년 다시 계산"]
    limits = ["동별 체감 결핍 자료 없음 (경북·전국 조사 수치만)", "의료 한 분야만 분석 · 현장 검증 자료 없음",
              "다른 도시에 쓰려면 읍면동별 외국인 주민 자료 필요"]
    for x, head, items, ic, color, fill in [(0.7, "기대 효과", goods, "check", "teal", TEAL_SOFT),
                                            (6.82, "한계", limits, "warn", "coral", SOFT)]:
        rect(s, x, 1.95, 5.81, 4.55, fill)
        txt(s, x + 0.45, 2.3, 5, 0.5, [(head, 22, True, NAVY)], after=0)
        for i, t in enumerate(items):
            y = 3.15 + i * 1.0
            icon(s, ic, color, x + 0.45, y + 0.06, 0.3)
            txt(s, x + 0.95, y, 4.6, 0.85, [(t, 16, False, INK)], after=0)

    # 12. Q&A (원본 10)
    s = blank(prs, dark=True)
    txt(s, 0, 2.3, W, 1.3, [("Q & A", 66, True, WHITE)], anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, after=0)
    txt(s, 0, 3.65, W, 0.5, [("데이터로 구현하는 포항시의 포용적 미래", 20, False, MUTED)], align=PP_ALIGN.CENTER, after=0)
    rect(s, 3.4, 4.55, 6.53, 0.7, NAVY_2, line=RGBColor(0x33, 0x41, 0x55), radius=0.5)
    txt(s, 3.4, 4.55, 6.53, 0.7, [("포항시 외국인 주민 생활 인프라 격차 진단·정책 후보 생성 시스템", 15, False, TEAL_BRIGHT)],
        anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, after=0)

    # 13. 이미지 출처 (원본 11 — 지금 쓰는 사진만 남김)
    s = content(prs, "출처", "Image Sources", 13)
    add_cover(s, ROOT / "outputs/figures/problem_photo.jpg", 0.7, 1.95, 1.3, 0.85)
    txt(s, 2.25, 1.95, 10.3, 0.85, [("https://knic.com.vn/wp-content/uploads/2026/05/KCN-Bau-Can_NK_PCTT_fr2-1.jpg", 12, False, SLATE),
                                   ("Source: knic.com.vn", 12, True, TEAL)], anchor=MSO_ANCHOR.MIDDLE, after=3)
    txt(s, 0.7, 3.2, 11.9, 0.5, [("지도·대시보드·정책 카드는 이 프로젝트의 분석 결과로 직접 만들었습니다. 아이콘: Font Awesome (react-icons)", 12, False, SLATE)], after=0)

    prs.save(OUT)
    print(f"저장됨: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
