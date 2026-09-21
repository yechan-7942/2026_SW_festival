"""M4 대체 데이터 — 행정동별 수요 신호가 아니라 LLM 정책 리포트에 주입할 고정 배경 근거.

MDIS 원자료는 비용 문제로 포기했고(reports/m4_nlp_substitute.md), 대신 공개
결과보고서 2건(data/MANIFEST.yaml 등록)에서 뽑은 수치다. 둘 다 행정동 단위
분해는 없어 gap_score(M3)처럼 행정동별 값이 아니라, 고정 컨텍스트로만 쓴다.

1. 여가부 「2024년 전국다문화가족실태조사」— 전국 집계치. 연구진이 직접
   "시도별 RSE값이 불안정해 시도별 분석은 시도하지 않았다"고 명시한 자료라
   전국 단위가 한계다.
2. 경상북도 「외국인주민 및 다문화가족 실태조사」(2023, 이민정책연구원) —
   경북을 5개 권역으로 나눠 분석했고 포항시는 "1권역"(포항시·경주시·영천시·
   경산시·청도군)에 속한다. 순수 포항 단독은 아니지만 전국 집계치보다
   지리적으로 훨씬 가깝다 — 우선순위를 더 높게 둔다.
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

# === 경상북도 조사 (포항시가 속한 "1권역" 포함, 여가부 전국치보다 우선) ===

GYEONGBUK_SURVEY_CITATION = (
    "경상북도 외국인주민 및 다문화가족 실태조사 (경상북도, 이민정책연구원 수행, 2023.11). "
    "경북을 5개 권역으로 나눠 분석 — 포항시는 1권역(포항시·경주시·영천시·경산시·청도군)에 속함. "
    "순수 포항 단독 수치는 아니지만 전국치보다 지리적으로 가까움."
)

# 경상북도 거주 시 어려운 점 (5점 척도, 점수 높을수록 어려움 큼, 경북 전체 기준)
GYEONGBUK_LIFE_DIFFICULTIES = {
    "언어 문제": 3.25,
    "비자 등 체류자격 취득 및 변경": 3.13,
    "경제활동 기회 획득": 3.06,
    "한국인과 관계맺기": 3.06,
    "자녀 양육 및 교육": 3.05,
    "외국인에 대한 차별": 2.96,
    "지식 및 기술 습득을 위한 교육기회 획득": 2.95,
    "공공행정기관을 통한 민원 제기 및 해결": 2.93,
    "한국식 생활문화 적응": 2.83,
    "의료기관 이용": 2.71,
    "주택 등 주거공간": 2.67,
    "음식": 2.64,
}

# 아파도 병원에 안/못 가고 다르게 대처하는 비율 — README §1 "의료" 도메인과 직결,
# 여가부 전국치("4.1%가 병원 못 감")보다 훨씬 구체적인 대처 행태 분해.
GYEONGBUK_HEALTHCARE_COPING = {
    "병원/의원(한의원) 이용": 0.623,
    "병원/의원 처방 없이 약국 이용": 0.164,
    "보건소 이용": 0.101,
    "본국에서 가져온 약 사용": 0.055,
    "외국인노동자 무료 진료소 이용": 0.026,
    "그냥 참는다": 0.021,
}

# 차별 경험률 — 경북/대구/전국 비교(전체 기준)
GYEONGBUK_DISCRIMINATION_RATE = 0.224
DAEGU_DISCRIMINATION_RATE = 0.249
NATIONAL_DISCRIMINATION_RATE_GB_SURVEY = 0.205  # 이 경북 조사가 인용한 전국치(여가부 조사의 0.130과는 다른 조사·문항)

# 교육 및 서비스: 이용 경험률 vs 요구도(5점 척도) — 경북 전체 기준. 의료상담이 별도 항목으로 있다.
GYEONGBUK_SERVICE_USAGE = {
    "한국어교육": 0.561,
    "한국사회 이해교육": 0.340,
    "의료상담 및 진료서비스": 0.312,
    "출입국·체류 관련 교육·상담": 0.297,
    "통번역 서비스": 0.280,
    "취업 관련 정보제공 및 일자리 소개": 0.243,
    "자격증 취득 및 취업교육": 0.236,
}
GYEONGBUK_SERVICE_DEMAND_SCORE = {
    "한국어교육": 4.42,
    "출입국·체류 관련 교육·상담": 4.14,
    "한국사회 이해교육": 4.09,
    "통번역 서비스": 4.05,
    "법률상담": 4.05,
    "취업 관련 정보제공 및 일자리 소개": 4.10,
    "의료상담 및 진료서비스": 4.02,
    "자격증 취득 및 취업교육": 4.02,
}


def build_context_block(fac_type: str | None = None) -> str:
    """LLM 프롬프트에 그대로 붙여넣을 배경 근거 텍스트를 만든다.

    경상북도 조사(포항이 속한 1권역 포함) 수치를 먼저·우선해서 보여주고, 여가부
    전국 조사는 보조 근거로 뒤에 붙인다 — 지리적으로 더 가까운 근거를 LLM이 먼저
    쓰게 유도하되, 둘 다 정확히 어느 조사에서 나온 수치인지 라벨을 붙여 섞이지
    않게 한다. fac_type을 주면(예: "의료") 해당 도메인과 직결된 통계를 맨 앞에
    강조한다. 항상 "포항 전용 수치 아님" caveat을 포함해 LLM이 이 수치를 포항
    고유값처럼 오인해 근거로 지어내지 않도록 한다.
    """
    lines = [f"[배경 근거 1 — {GYEONGBUK_SURVEY_CITATION}]"]

    if fac_type == "의료":
        lines.append("아파도 병원에 안/못 가고 다르게 대처한 비율 (경북, 대처 방식별):")
        for label, rate in sorted(GYEONGBUK_HEALTHCARE_COPING.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {label}: {rate:.1%}")
        lines.append(f"경북 거주 시 어려운 점 중 '의료기관 이용': 5점 척도 {GYEONGBUK_LIFE_DIFFICULTIES['의료기관 이용']:.2f}점")
        demand = GYEONGBUK_SERVICE_DEMAND_SCORE["의료상담 및 진료서비스"]
        usage = GYEONGBUK_SERVICE_USAGE["의료상담 및 진료서비스"]
        lines.append(f"의료상담 및 진료서비스: 이용 경험 {usage:.1%}, 요구도 5점 척도 {demand:.2f}점")

    lines.append("경상북도 거주 시 어려운 점 (5점 척도, 점수 높을수록 어려움 큼, 상위 항목):")
    for label, rate in sorted(GYEONGBUK_LIFE_DIFFICULTIES.items(), key=lambda kv: -kv[1])[:5]:
        lines.append(f"  - {label}: {rate:.2f}점")

    lines.append(
        f"차별 경험률: 경북 {GYEONGBUK_DISCRIMINATION_RATE:.1%} "
        f"(비교: 대구 {DAEGU_DISCRIMINATION_RATE:.1%}, 전국 {NATIONAL_DISCRIMINATION_RATE_GB_SURVEY:.1%} — 같은 조사 내 비교치)"
    )

    lines.append(f"\n[배경 근거 2 — {SURVEY_CITATION}]")

    if fac_type == "의료":
        lines.append(f"- 최근 1년간 아파도 병원에 가지 못한 경험이 있다는 응답(전국): {MEDICAL_ACCESS_BARRIER_RATE:.1%}")

    lines.append("한국생활 전반의 어려움 (전국, 복수 항목 중 상위):")
    for label, rate in sorted(LIFE_DIFFICULTIES.items(), key=lambda kv: -kv[1]):
        lines.append(f"  - {label}: {rate:.1%}")

    lines.append("자녀 양육 애로사항 1순위 (전국):")
    for age_group, items in CHILDCARE_DIFFICULTIES.items():
        top_label, top_rate = max(items.items(), key=lambda kv: kv[1])
        lines.append(f"  - {age_group}: {top_label} ({top_rate:.1%})")

    lines.append(f"차별 경험(전국): {DISCRIMINATION_RATE:.1%} (장소별 1위: 직장 {DISCRIMINATION_BY_SETTING['직장']:.1%})")

    demand_top = max(SUPPORT_SERVICE_DEMAND_SCORE.items(), key=lambda kv: kv[1])
    lines.append(
        f"지원서비스 실제 이용률({SUPPORT_SERVICE_USAGE_RATE:.1%})보다 요구도가 가장 높은 항목(전국): "
        f"{demand_top[0]} (5점 척도 {demand_top[1]:.2f}점) — 이용률-요구도 갭이 큰 서비스"
    )

    lines.append(
        "\n두 배경 근거 모두 포항 단독 수치가 아니다 — 근거 1은 경북 1권역(포항 포함 5개 시군) 또는 "
        "경북 전체, 근거 2는 전국 조사다. 포항 고유 데이터인 것처럼 서술하지 말고, "
        "'경북(권역) 조사에 따르면' 또는 '전국 조사에 따르면' 식으로 출처를 밝혀 인용할 것."
    )
    return "\n".join(lines)
