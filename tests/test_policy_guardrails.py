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
