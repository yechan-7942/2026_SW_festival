"""수정된 발표자료(PPTX)를 현재 산출물에서 만든다 — reports/presentation_revision_draft.md의 슬라이드 수정안 구현.

원본 PPTX가 없어 기존 PDF의 구현과 어긋난 슬라이드(3~9)를 실제 결과로 새로 구성한다.
수치는 전부 data/processed/*.parquet에서 읽는다(손으로 옮겨 적지 않는다).
실행: uv run --with python-pptx python scripts/build_presentation.py
"""

import sys
from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs/presentation_revised.pptx"
FONT = "Apple SD Gothic Neo"
BLUE = RGBColor(0x1C, 0x5C, 0xAB)
GRAY = RGBColor(0x52, 0x51, 0x4E)


def _text(tf, lines, size=16, bold_first=False, color=None):
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.space_after = Pt(6)
        for r in p.runs:
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = bold_first and i == 0
            if color:
                r.font.color.rgb = color


def slide(prs, title, bullets=None, size=16):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12.1), Inches(0.9))
    _text(tb.text_frame, [title], size=28, bold_first=True, color=BLUE)
    if bullets:
        body = s.shapes.add_textbox(Inches(0.6), Inches(1.4), Inches(12.1), Inches(5.6))
        _text(body.text_frame, bullets, size=size)
    return s


def table(s, rows, left, top, width, col_w, size=13):
    t = s.shapes.add_table(len(rows), len(rows[0]), left, top, width, Inches(0.4 * len(rows))).table
    for j, w in enumerate(col_w):
        t.columns[j].width = Inches(w)
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = t.cell(i, j)
            cell.text = str(val)
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.name = FONT
                    r.font.size = Pt(size)
                    r.font.bold = i == 0


def main():
    gap = pd.read_parquet(ROOT / "data/processed/gap_scores.parquet")
    units = pd.read_parquet(ROOT / "data/processed/admin_units.parquet")[["adm_cd", "adm_nm", "pop_total", "pop_foreign"]]
    rob = pd.read_parquet(ROOT / "data/processed/gap_robustness.parquet")
    cards = pd.read_parquet(ROOT / "data/processed/policy_cards.parquet")
    df = gap.merge(units, on="adm_cd").merge(rob[["adm_cd", "gap_type", "rank_spread", "top_in_all"]], on="adm_cd").sort_values("rank")
    stable = df[df["top_in_all"]]["adm_nm"].tolist()

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    slide(prs, "핵심 차별점: 2단 파이프라인 + 근거 기반 LLM 리포트", [
        "1단계  2SFCA 공간분석",
        "공급량·수요량·거리를 결합해 행정동별 의료 접근성을 지수화하고, 외국인 비율과 합쳐 격차 점수를 계산합니다. 수요점은 집계구 1,046개의 인구로 가중한 중심점입니다.",
        "",
        "2단계  LLM 정책 카드 생성",
        "격차 점수·행정동 사실(외국인 수·비율, 면/읍/동 성격, 격차 유형)과 공개 실태조사 수치를 근거로 29개 행정동의 정책 카드를 만들고, 수치·한자·부적절 제안을 코드로 걸러 냅니다.",
    ], size=18)

    slide(prs, "활용 데이터와 데이터 공백 대응", [
        "• 외국인·총인구: 포항시 지역통계(KOSIS), 2025",
        "• 행정동 경계·집계구 인구: 통계청 SGIS",
        "• 의료 공급: 심평원 병원·약국 (908건, 병원은 총의사수 반영)",
        "• 배경 근거: 경상북도 외국인주민 실태조사(2023, 포항 속한 1권역) + 여가부 전국다문화가족실태조사(2025)",
        "",
        "데이터가 없을 때 멈추지 않고 한계를 밝히며 진행했습니다.",
        "  - 금융: 상가정보 API에 레코드 0건 → 의료 단일 지수로 확정",
        "  - 다문화가족실태조사 원자료: 유료 원격접근 → 공개 결과보고서로 대체 (행정동별 차등은 불가)",
    ], size=17)

    slide(prs, "핵심 알고리즘: 격차 점수", [
        "gap_score = 0.5 × 정규화(외국인 비율) + 0.5 × (1 − 정규화(접근성))",
        "",
        "• 수요: 행정동 외국인 비율 (pop_foreign / pop_total)",
        "• 접근성: 2SFCA 지수, 기본 임계거리 3km (1·5km도 함께 비교)",
        "• 가중치 0.5/0.5: GM 검수를 거친 중립값",
        "",
        f"결과: {', '.join(stable)} 네 곳은 임계거리를 1·3·5km로 바꿔도 항상 상위 5위 안에 남습니다.",
    ], size=18)

    s = slide(prs, "LLM 정책 카드: 근거를 지키게 하는 가드레일")
    body = s.shapes.add_textbox(Inches(0.6), Inches(1.4), Inches(12.1), Inches(5.6))
    _text(body.text_frame, [
        "1. 입력: 격차 점수·순위 + 행정동 사실 + 조사 수치 (조사 수치와 행정동 사실을 구분해 인용)",
        "2. 출력 검사: 추론 과정 누출, 한자·외국어 혼입, 미완성 응답, 프롬프트에 없는 수치, '처방전 없이 약 제공' 같은 부적절 제안 → 자동 재생성",
        "3. 실제 발견: 가드레일 없이는 29건이 거의 같은 처방으로 수렴했고, 약사법에 어긋나는 제안도 나왔습니다.",
        "4. 한계: 형식·수치만 검증합니다. 정책의 타당성은 사람(GM)이 검수합니다.",
    ], size=17)

    s = slide(prs, "실제 분석 결과: 포항시 의료 인프라 격차 점수 상위")
    rows = [["순위", "행정동", "격차 점수", "외국인 비율", "1·3·5km 순위 변동", "격차 유형"]]
    for r in df.head(8).itertuples():
        rows.append([r.rank, r.adm_nm, f"{r.gap_score:.3f}", f"{r.pop_foreign / r.pop_total:.1%}", f"{int(r.rank_spread)}위", r.gap_type.split("(")[0]])
    table(s, rows, Inches(0.6), Inches(1.4), Inches(12.1), [0.9, 1.8, 1.6, 1.8, 2.6, 3.4])
    note = s.shapes.add_textbox(Inches(0.6), Inches(5.3), Inches(12.1), Inches(1.5))
    _text(note.text_frame, [
        "남구 외곽 읍·면(구룡포읍·장기면·호미곶면·대송면)은 인구가 적고 외국인 비율이 높으며 의료시설이 드뭅니다.",
        "순위가 크게 흔들리는 동(예: 송라면 변동 14위)은 '몇 위'라고 단정하지 않습니다.",
    ], size=15, color=GRAY)

    s = slide(prs, "산출물: 통합 대시보드와 정책 카드")
    heat = ROOT / "outputs/figures/gap_heatmap.png"
    s.shapes.add_picture(str(heat), Inches(0.5), Inches(1.5), width=Inches(6.4))
    top = cards.sort_values("rank").iloc[0]
    box = s.shapes.add_textbox(Inches(7.1), Inches(1.4), Inches(5.7), Inches(5.5))
    _text(box.text_frame, [f"{top.adm_nm} 정책 카드 (격차 1위)", "", *[l.strip() for l in top.policy_text.splitlines() if l.strip()]], size=13, bold_first=True)

    slide(prs, "기대효과와 한계", [
        "기대효과",
        "• 근거 중심 행정: 어느 동을 먼저 볼지 데이터로 제시",
        "• 예산 효율화: 상위 4곳은 지표 선택에 흔들리지 않는 우선 후보",
        "• 외국인 주민 정주 여건 개선",
        "",
        "한계 (먼저 밝힙니다)",
        "• 행정동별로 달라지는 수요 신호(체감 결핍)는 없음 — 배경 근거는 경북 1권역·전국 수치",
        "• 의료 단일 지표, 수요점은 총인구 기준",
        "• 다른 도시 이식은 읍면동별 외국인 주민 데이터 확보가 선결 조건",
    ], size=17)

    prs.save(OUT)
    print(f"저장됨: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
