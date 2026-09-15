from pathlib import Path

import pandas as pd
import pytest

PROCESSED_DATA_AVAILABLE = (
    Path("data/processed/admin_units.parquet").exists() and Path("data/processed/accessibility.parquet").exists()
)

pytestmark = pytest.mark.skipif(
    not PROCESSED_DATA_AVAILABLE,
    reason="data/processed/admin_units.parquet 또는 accessibility.parquet가 없음 — 먼저 access 파이프라인을 실행해야 함",
)


def test_load_weights_sums_to_one():
    from src.gap.score import load_weights

    weights = load_weights()
    assert weights["demand_weight"] + weights["access_weight"] == pytest.approx(1.0)


def test_load_weights_rejects_bad_sum(tmp_path):
    from src.gap.score import load_weights

    bad_path = tmp_path / "weights.yaml"
    bad_path.write_text(
        "gap_score:\n  demand_weight: 0.7\n  access_weight: 0.7\n  demand_metric: pop_foreign_ratio\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_weights(str(bad_path))


def test_min_max_normalize_handles_constant_series():
    from src.gap.score import min_max_normalize

    constant = pd.Series([3.0, 3.0, 3.0])
    normalized = min_max_normalize(constant)
    assert (normalized == 0.5).all()


def test_min_max_normalize_range():
    from src.gap.score import min_max_normalize

    series = pd.Series([1.0, 2.0, 5.0, 10.0])
    normalized = min_max_normalize(series)
    assert normalized.min() == pytest.approx(0.0)
    assert normalized.max() == pytest.approx(1.0)


def test_build_gap_scores_returns_29_dong_with_contract_columns():
    from src.gap.score import build_gap_scores

    result = build_gap_scores()
    assert len(result) == 29
    assert list(result.columns) == ["adm_cd", "fac_type", "gap_score", "rank", "cluster_id"]
    assert result["adm_cd"].is_unique
    assert (result["fac_type"] == "보건의료").all()


def test_gap_score_is_bounded_zero_to_one():
    from src.gap.score import build_gap_scores

    result = build_gap_scores()
    assert (result["gap_score"] >= 0).all()
    assert (result["gap_score"] <= 1).all()


def test_rank_is_dense_1_to_n():
    from src.gap.score import build_gap_scores

    result = build_gap_scores()
    assert result["rank"].min() == 1
    assert result["rank"].max() <= len(result)


def test_cluster_id_is_one_of_four_tiers():
    from src.gap.score import build_gap_scores

    result = build_gap_scores()
    assert set(result["cluster_id"].unique()).issubset({1, 2, 3, 4})


def test_high_demand_low_access_yields_highest_gap_score():
    """합성 데이터로 공식의 방향성만 검증 — 수요 높고 접근성 낮은 동이 1등이어야 한다."""
    from src.gap.score import compute_gap_scores

    accessibility = pd.DataFrame(
        {
            "adm_cd": ["A", "B", "C"],
            "fac_type": ["보건의료"] * 3,
            "access_index": [0.001, 0.01, 0.02],  # A가 접근성 최악
        }
    )
    admin_units = pd.DataFrame(
        {
            "adm_cd": ["A", "B", "C"],
            "pop_total": [1000, 1000, 1000],
            "pop_foreign": [500, 100, 50],  # A가 외국인 비율 최고
        }
    )
    admin_units_path = "tests/_tmp_admin_units_for_gap_test.parquet"
    admin_units.to_parquet(admin_units_path)
    try:
        result = compute_gap_scores(accessibility, admin_units_path=admin_units_path)
    finally:
        Path(admin_units_path).unlink()

    top = result.sort_values("rank").iloc[0]
    assert top["adm_cd"] == "A"


def test_compute_gap_scores_raises_for_adm_cd_missing_from_admin_units():
    from src.gap.score import compute_gap_scores

    accessibility = pd.DataFrame(
        {"adm_cd": ["존재하지않는코드"], "fac_type": ["보건의료"], "access_index": [0.01]}
    )
    with pytest.raises(ValueError):
        compute_gap_scores(accessibility)


def test_gap_score_sensitivity_covers_configured_thresholds():
    from src.access.catchment import load_config
    from src.gap.score import gap_score_sensitivity

    config = load_config()
    thresholds = config["distance_thresholds_km"]

    sens = gap_score_sensitivity("보건의료")
    assert len(sens) == 29
    for threshold_km in thresholds:
        assert f"{threshold_km}km_gap_score" in sens.columns
        assert f"{threshold_km}km_rank" in sens.columns
