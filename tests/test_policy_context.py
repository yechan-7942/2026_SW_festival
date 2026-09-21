from src.policy.context import (
    CHILDCARE_DIFFICULTIES,
    DISCRIMINATION_BY_SETTING,
    DISCRIMINATION_RATE,
    LIFE_DIFFICULTIES,
    MEDICAL_ACCESS_BARRIER_RATE,
    SUPPORT_SERVICE_DEMAND_SCORE,
    build_context_block,
)


def test_life_difficulties_are_valid_rates():
    assert LIFE_DIFFICULTIES
    for rate in LIFE_DIFFICULTIES.values():
        assert 0.0 <= rate <= 1.0


def test_medical_access_barrier_rate_is_valid():
    assert 0.0 <= MEDICAL_ACCESS_BARRIER_RATE <= 1.0


def test_childcare_difficulties_cover_both_age_groups():
    assert set(CHILDCARE_DIFFICULTIES) == {"5세 이하", "6~24세"}
    for items in CHILDCARE_DIFFICULTIES.values():
        assert items


def test_discrimination_setting_rates_are_valid():
    assert 0.0 <= DISCRIMINATION_RATE <= 1.0
    for rate in DISCRIMINATION_BY_SETTING.values():
        assert 0.0 <= rate <= 1.0


def test_build_context_block_includes_citation_and_caveat():
    block = build_context_block()
    assert "전국다문화가족실태조사" in block
    assert "포항이 아닌 전국" in block


def test_build_context_block_highlights_medical_stat_for_medical_fac_type():
    block = build_context_block(fac_type="의료")
    medical_line_idx = next(i for i, line in enumerate(block.splitlines()) if "병원에 가지 못한" in line)
    difficulties_header_idx = next(i for i, line in enumerate(block.splitlines()) if "한국생활 전반의 어려움" in line)
    assert medical_line_idx < difficulties_header_idx


def test_build_context_block_omits_medical_line_without_fac_type():
    block = build_context_block()
    assert "병원에 가지 못한" not in block


def test_support_service_demand_scores_are_on_five_point_scale():
    for score in SUPPORT_SERVICE_DEMAND_SCORE.values():
        assert 0.0 <= score <= 5.0
