"""수정된 발표자료(PPTX)를 현재 산출물에서 만든다 — reports/presentation_revision_draft.md의 슬라이드 수정안 구현.

원본 PPTX가 없어 기존 PDF의 구현과 어긋난 슬라이드(3~9)를 실제 결과로 새로 구성한다.
수치는 전부 data/processed/*.parquet에서 읽는다(손으로 옮겨 적지 않는다).
디자인은 원본 PDF(청록 포인트, 카드형)에 맞췄고, 문구는 발표할 때 말하는 투로 짧게 썼다.
실행: uv run --with python-pptx python scripts/build_presentation.py
"""

import sys
from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.access.network import compare_road_vs_straight  # noqa: E402
from src.gap.validation import access_rank_of, compare_access_indicators, road_gap_comparison, weight_sweep  # noqa: E402
OUT = ROOT / "outputs/presentation_revised.pptx"
FONT = "Apple SD Gothic Neo"
TEAL = RGBColor(0x0F, 0x96, 0x88)
TEAL_LIGHT = RGBColor(0x8F, 0xD0, 0xC8)
INK = RGBColor(0x14, 0x1E, 0x2E)
GRAY = RGBColor(0x55, 0x5F, 0x6D)
CARD = RGBColor(0xF6, 0xF8, 0xFA)
EDGE = RGBColor(0xE2, 0xE6, 0xEA)
MINT = RGBColor(0xEC, 0xF8, 0xF5)


def box(s, x, y, w, h, fill=CARD, line=EDGE, shape=MSO_SHAPE.RECTANGLE):
    r = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    r.fill.solid()
    r.fill.fore_color.rgb = fill
    if line is None:
        r.line.fill.background()
    else:
        r.line.color.rgb = line
        r.line.width = Pt(0.75)
    r.shadow.inherit = False
    return r


def text(s, x, y, w, h, paras, anchor=MSO_ANCHOR.TOP):
    """paras: [(문장, 글자크기, 굵게, 색)] 목록."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, (t, size, bold, color) in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = t
        p.space_after = Pt(5)
        for r in p.runs:
            r.font.name, r.font.size, r.font.bold, r.font.color.rgb = FONT, Pt(size), bold, color
    return tb


def new_slide(prs, title):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    box(s, 0.6, 0.45, 0.08, 0.6, fill=TEAL, line=None)
    text(s, 0.85, 0.38, 11.5, 0.8, [(title, 28, True, INK)], anchor=MSO_ANCHOR.MIDDLE)
    return s


def card(s, x, y, w, h, head, body, body_size=15):
    box(s, x, y, w, h)
    box(s, x, y, w, 0.07, fill=TEAL, line=None)
    text(s, x + 0.25, y + 0.3, w - 0.5, h - 0.4, [(head, 19, True, INK), (body, body_size, False, GRAY)])


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

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    # 1. 차별점
    s = new_slide(prs, "이 프로젝트가 다른 점")
    card(s, 0.6, 1.8, 5.95, 2.5, "1. 부족한 곳을 찾는다",
         "병원·약국 위치와 인구를 놓고 동마다 의료 접근성을 계산했습니다. 여기에 외국인 비율을 겹쳐서, 어느 동이 가장 아쉬운지 점수로 뽑았습니다.", 17)
    card(s, 6.78, 1.8, 5.95, 2.5, "2. 정책 초안을 쓴다",
         f"점수와 동별 사정을 LLM에 넘겨서 {n_dong}개 동의 정책 카드를 썼습니다. 숫자가 틀리거나 이상한 제안이 나오면 코드가 걸러내고 다시 쓰게 했습니다.", 17)
    text(s, 0.6, 4.7, 12.1, 0.8, [("수요 위치는 집계구 1,046개의 인구로 가중한 중심점을 썼습니다.", 15, False, GRAY)])

    # 2. 데이터
    s = new_slide(prs, "어떤 데이터를 썼나")
    card(s, 0.6, 1.6, 3.9, 2.6, "외국인·인구", "포항시 지역통계(KOSIS) 2025년. 동 경계와 집계구 인구는 통계청 SGIS에서 받았습니다.")
    card(s, 4.72, 1.6, 3.9, 2.6, "병원·약국", "심평원 자료 908곳. 병원은 의사 수까지 반영했습니다.")
    card(s, 8.83, 1.6, 3.9, 2.6, "배경 조사", "경북 외국인주민 실태조사(2023), 여가부 전국다문화가족실태조사(2025)의 공개 결과.")
    box(s, 0.6, 4.6, 12.13, 1.9, fill=MINT, line=None)
    text(s, 0.9, 4.75, 11.5, 1.7, [
        ("못 구한 데이터도 있었습니다", 17, True, TEAL),
        ("금융은 상가정보 API에 포항 데이터가 0건이라 빼고 의료만 봤습니다. 다문화가족실태조사 원자료는 유료라 공개 보고서로 바꿨습니다. 그래서 동마다 다른 체감 차이는 이번 분석에 들어 있지 않습니다.", 15, False, INK),
    ])

    # 3. 격차 점수
    s = new_slide(prs, "격차 점수는 이렇게 계산했습니다")
    box(s, 0.6, 1.6, 12.13, 1.5, fill=MINT, line=None)
    text(s, 0.6, 1.6, 12.13, 1.5, [
        ("격차 점수 = 0.5 × 외국인 비율 + 0.5 × (1 − 의료 접근성)", 26, True, INK),
        ("두 값 모두 0에서 1 사이로 맞춘 뒤 계산", 14, False, GRAY),
    ], anchor=MSO_ANCHOR.MIDDLE)
    card(s, 0.6, 3.4, 3.9, 2.2, "외국인 비율", "동 인구 중 외국인이 차지하는 비율입니다.")
    card(s, 4.72, 3.4, 3.9, 2.2, "의료 접근성", "2SFCA 지수입니다. 기준 거리는 3km로 하고, 1km와 5km로도 돌려봤습니다.")
    card(s, 8.83, 3.4, 3.9, 2.2, "가중치 0.5 : 0.5", "한쪽에 무게를 둘 근거가 없어서 같게 뒀고, GM 검수를 받았습니다.")
    text(s, 0.6, 5.95, 12.1, 0.9, [
        (f"기준 거리를 1·3·5km로 바꿔도 {', '.join(stable)}은 늘 5위 안에 들었습니다.", 18, True, TEAL)])

    # 4. 정책 카드
    s = new_slide(prs, "정책 카드는 이렇게 걸러냈습니다")
    card(s, 0.6, 1.6, 3.9, 3.3, "넣는 것",
         "격차 점수, 동별 사실(외국인 수, 읍·면·동 구분), 조사 수치. 조사 수치와 동 사실은 구분해서 인용하게 했습니다.", 14)
    card(s, 4.72, 1.6, 3.9, 3.3, "거르는 것",
         "추론 과정이 새어 나온 글, 한자·외국어가 섞인 글, 중간에 잘린 글, 입력에 없는 숫자, '처방전 없이 약 제공' 같은 제안은 다시 쓰게 했습니다.", 14)
    card(s, 8.83, 1.6, 3.9, 3.3, "사람이 보는 것",
         "코드는 형식과 숫자만 확인합니다. 정책이 실제로 타당한지는 GM이 검수합니다.", 14)
    box(s, 0.6, 5.3, 12.13, 1.3, fill=MINT, line=None)
    text(s, 0.9, 5.3, 11.5, 1.3, [
        (f"처음엔 거르는 장치 없이 돌렸는데, {n_dong}건이 거의 같은 처방으로 나왔고 약사법에 맞지 않는 제안도 있었습니다.", 16, True, INK)],
        anchor=MSO_ANCHOR.MIDDLE)

    # 5. 결과
    s = new_slide(prs, "결과: 남구 외곽 읍·면 4곳이 먼저 볼 곳")
    top = df.head(8)
    cd = CategoryChartData()
    cd.categories = list(top["adm_nm"])[::-1]
    cd.add_series("격차 점수", [round(v, 3) for v in top["gap_score"]][::-1])
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.5), Inches(1.5), Inches(7.4), Inches(5.3), cd)
    ch = gf.chart
    ch.has_legend = False
    ch.has_title = False
    ch.value_axis.visible = False
    ch.value_axis.has_major_gridlines = False
    ch.value_axis.maximum_scale = 1.05
    ch.value_axis.minimum_scale = 0
    ch.category_axis.tick_labels.font.size = Pt(14)
    ch.category_axis.tick_labels.font.name = FONT
    ch.category_axis.format.line.fill.background()
    plot = ch.plots[0]
    plot.gap_width = 45
    plot.has_data_labels = True
    plot.data_labels.number_format = "0.00"
    plot.data_labels.number_format_is_linked = False
    plot.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
    plot.data_labels.font.size = Pt(13)
    plot.data_labels.font.name = FONT
    plot.series[0].format.fill.solid()
    plot.series[0].format.fill.fore_color.rgb = TEAL_LIGHT
    flags = list(top["top_in_all"])[::-1]
    for i, f in enumerate(flags):
        pt = plot.series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = TEAL if f else TEAL_LIGHT
    box(s, 8.2, 1.6, 4.53, 2.3, fill=MINT, line=None)
    text(s, 8.45, 1.7, 4.1, 2.1, [
        (f"{n_stable}곳", 40, True, TEAL),
        ("기준 거리를 바꿔도 5위 안에 남은 동 (진한 막대)", 14, False, INK),
    ], anchor=MSO_ANCHOR.MIDDLE)
    text(s, 8.2, 4.15, 4.53, 2.6, [
        (f"{', '.join(stable)}은 포항에서 외국인 비율이 가장 높은 네 곳이고, 의료 접근성은 중간 이하입니다.", 15, False, INK),
        (f"반대로 {jumpy.adm_nm}처럼 기준 거리에 따라 순위가 {int(jumpy.rank_spread)}계단 움직이는 동은 몇 위라고 말하지 않았습니다.", 15, False, GRAY),
    ])

    # 6. 검증 — reports/m6_validation.md
    s = new_slide(prs, "다른 방법으로도 비교해 봤습니다")

    def check(x, head, big, body):
        box(s, x, 1.6, 3.9, 2.8)
        box(s, x, 1.6, 3.9, 0.07, fill=TEAL, line=None)
        text(s, x + 0.25, 1.85, 3.4, 2.5, [(head, 17, True, INK), (big, 28, True, TEAL), (body, 13, False, GRAY)])

    check(0.6, "가중치를 바꿔도", f"{min_ok_weight:.1f} 이상",
          f"외국인 비율 가중치를 0부터 1까지 바꿔 봤습니다. {min_ok_weight:.1f} 이상이면 1·3·5km 어디서든 {n_stable}곳이 5위 안입니다. 접근성만 크게 보면 밀려납니다.")
    check(4.72, "접근성 지표를 바꿔도", f"{len(alt_rho)}개 {'모두' if n_alt_agree == len(alt) else f'중 {n_alt_agree - 1}개'}",
          f"E2SFCA, 3km 안 의사 수, 가장 가까운 병원까지 거리, 동 안 의사 수로 바꿔 봤습니다. 2SFCA와 순위상관은 {alt_rho.min():.2f}~{alt_rho.max():.2f}입니다.")
    check(8.83, "도로 거리로 재도", "1·3·5km 모두" if n_road_agree == len(road_cmp) else f"{len(road_cmp)}개 중 {n_road_agree}개",
          f"실제 도로는 직선보다 {detour:.2f}배쯤 멉니다. 도로 거리로 1·3·5km를 다시 계산해도 순위상관이 {road_cmp['spearman'].min():.2f} 이상이었습니다.")
    box(s, 0.6, 4.75, 12.13, 1.5, fill=MINT, line=None)
    text(s, 0.9, 4.8, 11.5, 1.4, [
        (f"다만 {n_stable}곳이 계속 남는 건 주로 외국인 비율이 높아서입니다.", 16, True, INK),
        (f"접근성만 따로 보면 29개 동 중 {access_ranks.min().min()}위~{access_ranks.max().max()}위로 흩어집니다(1위가 가장 나쁨). "
         "그래서 '병원이 가장 부족한 곳'이 아니라 '먼저 살펴볼 곳'으로 봅니다. 정답 데이터가 없어 정확도를 잰 것은 아닙니다.", 14, False, INK),
    ], anchor=MSO_ANCHOR.MIDDLE)

    # 7. 산출물
    s = new_slide(prs, "만든 결과물")
    s.shapes.add_picture(str(ROOT / "outputs/figures/gap_heatmap.png"), Inches(0.5), Inches(1.5), width=Inches(6.6))
    first = cards.sort_values("rank").iloc[0]
    box(s, 7.4, 1.6, 5.33, 5.2)
    box(s, 7.4, 1.6, 5.33, 0.07, fill=TEAL, line=None)
    body = [l.strip() for l in first.policy_text.splitlines() if l.strip()]
    text(s, 7.65, 1.85, 4.85, 4.9, [(f"{first.adm_nm} 정책 카드 (격차 1위)", 17, True, INK)] + [(l, 12, False, GRAY) for l in body])

    # 8. 기대효과와 한계
    s = new_slide(prs, "기대하는 점과 한계")
    box(s, 0.6, 1.6, 5.95, 4.9)
    box(s, 0.6, 1.6, 5.95, 0.07, fill=TEAL, line=None)
    text(s, 0.9, 1.9, 5.4, 4.5, [
        ("기대하는 점", 20, True, TEAL),
        ("어느 동부터 살펴볼지 데이터로 정할 수 있습니다.", 16, False, INK),
        (f"상위 {n_stable}곳은 기준을 바꿔도 흔들리지 않아서, 예산을 먼저 쓸 후보로 볼 수 있습니다.", 16, False, INK),
        ("외국인 주민이 병원을 이용하기 편해지는 쪽으로 이어집니다.", 16, False, INK),
    ])
    box(s, 6.78, 1.6, 5.95, 4.9)
    box(s, 6.78, 1.6, 5.95, 0.07, fill=GRAY, line=None)
    text(s, 7.08, 1.9, 5.4, 4.5, [
        ("한계", 20, True, GRAY),
        ("동마다 다른 체감 결핍은 담지 못했습니다. 배경 조사는 경북 1권역·전국 수치입니다.", 16, False, INK),
        ("지표는 의료 하나뿐입니다. 방법을 바꿔 비교는 했지만, 실제 불편과 맞는지 확인할 현장 자료는 아직 없습니다.", 16, False, INK),
        ("다른 도시에 쓰려면 읍면동별 외국인 주민 자료가 먼저 있어야 합니다.", 16, False, INK),
    ])

    prs.save(OUT)
    print(f"저장됨: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
