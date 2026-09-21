"""M4 대체 데이터 — 행정동별 수요 신호가 아니라 LLM 정책 리포트에 주입할 전국 단위 배경 근거.

MDIS 원자료는 비용 문제로 포기했고(reports/m4_nlp_substitute.md), 대신 여가부
「2024년 전국다문화가족실태조사 연구」결과보고서(data/MANIFEST.yaml 등록)에서 뽑은
수치다. 연구진이 직접 "시도별 RSE값이 불안정해 시도별 분석은 시도하지 않았다"고
명시한 자료라 행정동은커녕 경북 단위로도 못 쪼갠다 — 그래서 gap_score(M3)처럼
행정동별 값이 아니라, 포항 전체에 동일하게 적용하는 고정 컨텍스트로만 쓴다.
"""

SURVEY_CITATION = (
    "2024년 전국다문화가족실태조사 연구 (한국여성정책연구원, 여성가족부 연구용역, 2025.5). "
    "전국 결혼이민자·귀화자 16,014가구 대상 — 포항·경북 지역 통계가 아니라 전국 집계치."
)

# 지난 1년간 한국생활 전반의 어려움 (복수응답 아님, 어려움 없음 37.7%는 제외)
LIFE_DIFFICULTIES = {
    "경제적 어려움": 0.229,
    "언어 문제": 0.203,
    "외로움": 0.197,
    "생활방식·관습·음식 등 문화차이": 0.196,
    "가족 간 갈등": 0.060,
    "친구·이웃 사귀기": 0.059,
    "편견과 차별": 0.055,
    "공공기관이나 은행 이용": 0.037,
}

# 지난 1년간 아파서 병원에 가고 싶을 때 가지 못한 경험 — README §1 "의료" 도메인과 직결
MEDICAL_ACCESS_BARRIER_RATE = 0.041

# 자녀 양육 애로사항 1순위 (연령대별로 문항이 다름)
CHILDCARE_DIFFICULTIES = {
    "5세 이하": {
        "돌봐줄 사람을 찾기 어려움": 0.246,
        "자녀 양육에 대해 배우자·가족과 의견 차이": 0.216,
        "자녀에게 한국어를 직접 가르치기 어려움": 0.178,
    },
    "6~24세": {
        "교육비 부담": 0.249,
        "진학·진로 정보 부족": 0.218,
        "자녀 학습 지도 어려움": 0.197,
    },
}

# 차별 경험(13.0%)을 겪은 응답자의 장소별 비율(복수응답)
DISCRIMINATION_RATE = 0.130
DISCRIMINATION_BY_SETTING = {
    "직장": 0.746,
    "거리·동네": 0.535,
    "자녀의 학교나 보육시설": 0.340,
    "대중교통": 0.305,
    "공공기관": 0.275,
    "가족·친척": 0.240,
}

# 지원서비스 실제 이용률 vs 요구도(5점 척도) — 갭이 클수록 "필요한데 못 받는" 서비스
SUPPORT_SERVICE_USAGE_RATE = 0.215  # 지난 1년간 각종 교육·지원을 받은 경험이 있는 비율
SUPPORT_SERVICE_DEMAND_SCORE = {
    "취업·창업 지원": 2.58,
    "한국어·한국사회 적응교육": 2.53,
    "가정방문을 통한 각종 교육": 2.47,
    "자녀학습지원·언어발달·이중언어지원": 1.92,
    "부모교육": 1.90,
}

ECONOMIC_SNAPSHOT = {
    "고용률": 0.627,
    "단순노무직·서비스직 종사 비율": 0.60,
    "월평균임금 300만원 미만 비율": 0.80,
}


def build_context_block(fac_type: str | None = None) -> str:
    """LLM 프롬프트에 그대로 붙여넣을 배경 근거 텍스트를 만든다.

    fac_type을 주면(예: "의료") 해당 도메인과 직결된 통계를 맨 앞에 강조한다.
    항상 SURVEY_CITATION과 "전국 집계치, 포항 전용 수치 아님" caveat을 포함해
    LLM이 이 수치를 포항 고유값처럼 오인해 근거로 지어내지 않도록 한다.
    """
    lines = [f"[배경 근거 — {SURVEY_CITATION}]"]

    if fac_type == "의료":
        lines.append(f"- 최근 1년간 아파도 병원에 가지 못한 경험이 있다는 응답: {MEDICAL_ACCESS_BARRIER_RATE:.1%}")

    lines.append("한국생활 전반의 어려움 (전국, 복수 항목 중 상위):")
    for label, rate in sorted(LIFE_DIFFICULTIES.items(), key=lambda kv: -kv[1]):
        lines.append(f"  - {label}: {rate:.1%}")

    lines.append("자녀 양육 애로사항 1순위 (전국):")
    for age_group, items in CHILDCARE_DIFFICULTIES.items():
        top_label, top_rate = max(items.items(), key=lambda kv: kv[1])
        lines.append(f"  - {age_group}: {top_label} ({top_rate:.1%})")

    lines.append(f"차별 경험: {DISCRIMINATION_RATE:.1%} (장소별 1위: 직장 {DISCRIMINATION_BY_SETTING['직장']:.1%})")

    demand_top = max(SUPPORT_SERVICE_DEMAND_SCORE.items(), key=lambda kv: kv[1])
    lines.append(
        f"지원서비스 실제 이용률({SUPPORT_SERVICE_USAGE_RATE:.1%})보다 요구도가 가장 높은 항목: "
        f"{demand_top[0]} (5점 척도 {demand_top[1]:.2f}점) — 이용률-요구도 갭이 큰 서비스"
    )

    lines.append(
        "이 수치는 포항이 아닌 전국 결혼이민자·귀화자 조사 결과다. "
        "포항 고유 데이터인 것처럼 서술하지 말고, '전국 조사에 따르면' 식으로 인용할 것."
    )
    return "\n".join(lines)
