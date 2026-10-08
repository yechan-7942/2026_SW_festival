"""src/policy/report.py의 순수 함수 가드레일 테스트 — API 키 없이도 돈다.

실제로 관측된 결함(송도동 reasoning 누출, 여러 행정동 한자 혼입)을 재현한
회귀 테스트다. reports/m5_policy_llm.md 참고.
"""

from src.policy.report import KNOWN_HANJA_LEAKS, MIN_HANGUL_RATIO, _hangul_ratio


def test_hangul_ratio_is_one_for_pure_korean():
    assert _hangul_ratio("포항시 구룡포읍에 거주하는 외국인 주민") == 1.0


def test_hangul_ratio_is_zero_for_reasoning_leak():
    leaked = "We need to propose medical accessibility policy for foreign residents"
    assert _hangul_ratio(leaked) == 0.0


def test_hangul_ratio_below_threshold_for_english_reasoning():
    leaked = (
        "We need to produce 2 sentences in Korean, formal polite form. "
        "Must include a line starting with [근거] that cites the evidence."
    )
    assert _hangul_ratio(leaked) < MIN_HANGUL_RATIO


def test_known_hanja_leak_maps_to_correct_hangul():
    assert KNOWN_HANJA_LEAKS["設置"] == "설치"


def test_forbidden_pattern_blocks_prescription_free_drug_proposals_but_not_statistic_quotes():
    from src.policy.report import FORBIDDEN_PATTERN

    assert FORBIDDEN_PATTERN.search("약국과의 협약을 통해 처방전 없이도 기본 약품을 제공할 수 있는 프로그램")
    assert FORBIDDEN_PATTERN.search("약국과의 연계 처방전 없이 약물 제공 시범 사업")
    assert not FORBIDDEN_PATTERN.search("병원/의원 처방 없이 약국 이용 비율은 16.4%입니다.")


def test_ungrounded_numbers_flags_invented_values_but_allows_prompt_values_and_small_ints():
    from src.policy.report import ungrounded_numbers

    prompt = "병원/의원 이용 62.3%, 의료기관 이용 어려움 2.71점, 외국인 1,147명"
    assert ungrounded_numbers("이용률 62.3%, 주 2회 운영", prompt) == []
    assert ungrounded_numbers("이용률 71.5%에 달합니다", prompt) == ["71.5"]
    assert ungrounded_numbers("약 300명 규모", prompt) == ["300"]


def test_build_prompt_includes_dong_facts_and_style_rules():
    from src.policy.report import build_prompt

    facts = {"pop_foreign": 1147, "foreign_ratio": 0.1506, "foreign_ratio_rank": 2,
             "gap_type": "복합 취약형(수요 높음·접근성 낮음)", "rank_min": 1, "rank_max": 1}
    prompt = build_prompt("구룡포읍", 1, 0.945, "의료", facts)
    assert "읍 지역" in prompt and "1,147명" in prompt and "복합 취약형" in prompt
    assert "~합니다" in prompt and "처방전 없이" in prompt


def test_particle_glue_and_hanja_regexes():
    from src.policy.report import _HANJA_RE, _PARTICLE_GLUE_RE

    assert _PARTICLE_GLUE_RE.sub(r"\1 \2", "상담 창구를설치하고 게시판을 설치") == "상담 창구를 설치하고 게시판을 설치"
    assert _HANJA_RE.search("주민이 遠隔 마을에")
    assert not _HANJA_RE.search("주민이 먼 마을에")
