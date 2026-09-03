from pathlib import Path

import pytest

PROCESSED_DATA_AVAILABLE = (
    Path("data/processed/facilities.parquet").exists() and Path("data/processed/admin_units.parquet").exists()
)

pytestmark = pytest.mark.skipif(
    not PROCESSED_DATA_AVAILABLE,
    reason="data/processed/facilities.parquet 또는 admin_units.parquet가 없음 — 먼저 admin_join 파이프라인을 실행해야 함",
)


def test_two_sfca_returns_29_dong_with_contract_columns():
    from src.access.two_sfca import two_sfca

    result = two_sfca("보건의료", threshold_km=3)
    assert len(result) == 29
    assert list(result.columns) == ["adm_cd", "adm_nm", "fac_type", "access_index"]
    assert result["adm_cd"].is_unique
    assert (result["fac_type"] == "보건의료").all()


def test_access_index_is_never_negative():
    from src.access.two_sfca import two_sfca

    result = two_sfca("보건의료", threshold_km=3)
    assert (result["access_index"] >= 0).all()


def test_build_accessibility_skips_domain_with_no_supply():
    from src.access.two_sfca import build_accessibility

    combined = build_accessibility()
    assert set(combined["fac_type"].unique()) == {"보건의료"}
    assert list(combined.columns) == ["adm_cd", "fac_type", "access_index"]


def test_two_sfca_raises_for_domain_with_no_supply():
    from src.access.two_sfca import two_sfca

    with pytest.raises(ValueError):
        two_sfca("금융", threshold_km=3)


def test_distance_sensitivity_covers_configured_thresholds():
    from src.access.catchment import load_config
    from src.access.two_sfca import distance_sensitivity

    config = load_config()
    thresholds = config["distance_thresholds_km"]

    sens = distance_sensitivity("보건의료")
    assert len(sens) == 29
    for threshold_km in thresholds:
        assert f"{threshold_km}km" in sens.columns
        assert f"{threshold_km}km_rank" in sens.columns


def test_distance_weight_boundary_values():
    import numpy as np

    from src.access.two_sfca import distance_weight

    d_max = 5000.0
    weights = distance_weight(np.array([0.0, 2500.0, 5000.0, 6000.0]), d_max)
    assert weights[0] == pytest.approx(1.0)
    assert weights[1] == pytest.approx(0.5)
    assert weights[2] == pytest.approx(0.0)
    assert weights[3] == pytest.approx(0.0)  # d_max 밖은 0에서 클립


def test_two_sfca_e2sfca_returns_29_dong_with_contract_columns():
    from src.access.two_sfca import two_sfca_e2sfca

    result = two_sfca_e2sfca("보건의료", d_max_km=3)
    assert len(result) == 29
    assert list(result.columns) == ["adm_cd", "adm_nm", "fac_type", "access_index"]
    assert result["adm_cd"].is_unique
    assert (result["access_index"] >= 0).all()


def test_e2sfca_raises_for_domain_with_no_supply():
    from src.access.two_sfca import two_sfca_e2sfca

    with pytest.raises(ValueError):
        two_sfca_e2sfca("금융", d_max_km=3)


def test_compare_rank_volatility_shape():
    from src.access.two_sfca import compare_rank_volatility

    result = compare_rank_volatility("보건의료")
    assert result["total_dong"] == 29
    assert 0 <= result["vanilla_unstable_count"] <= 29
    assert 0 <= result["e2sfca_unstable_count"] <= 29
