from pathlib import Path

import pytest

PROCESSED_DATA_AVAILABLE = (
    Path("data/processed/admin_units.parquet").exists() and Path("data/processed/pop_centroids.parquet").exists()
)

pytestmark = pytest.mark.skipif(
    not PROCESSED_DATA_AVAILABLE,
    reason="data/processed 산출물이 없음 — 먼저 access 파이프라인을 실행해야 함",
)


@pytest.fixture(scope="module")
def sweep():
    from src.gap.validation import weight_sweep

    return weight_sweep()


def test_weight_sweep_covers_every_combination(sweep):
    # 임계거리 3개 × 가중치 11개, 조합마다 29개 동이 한 번씩
    assert sweep.groupby(["threshold_km", "demand_weight"]).size().eq(29).all()
    assert sweep.groupby(["threshold_km", "demand_weight"]).ngroups == 33


def test_weight_sweep_matches_official_gap_score_at_default():
    # 0.5 / 3km 조합은 공식 산출물 gap_scores.parquet과 같아야 한다
    import pandas as pd

    from src.gap.validation import weight_sweep

    official = pd.read_parquet("data/processed/gap_scores.parquet").set_index("adm_cd")["rank"]
    ours = weight_sweep(weights=[0.5])
    ours = ours[ours["threshold_km"] == 3].set_index("adm_cd")["rank"]
    assert ours.sort_index().equals(official.sort_index())


def test_top_n_share_is_between_zero_and_one(sweep):
    from src.gap.validation import top_n_share

    share = top_n_share(sweep)
    assert share.between(0, 1).all()
    assert share.is_monotonic_decreasing


def test_alternative_indicators_cover_all_dong():
    from src.gap.validation import alternative_access_indicators

    ind = alternative_access_indicators()
    assert len(ind) == 29
    assert not ind.isna().any().any()


def test_compare_access_indicators_baseline_is_self():
    from src.gap.validation import compare_access_indicators

    table = compare_access_indicators().set_index("indicator")
    assert table.loc["2SFCA", "spearman_vs_2sfca"] == pytest.approx(1.0)


def test_matrix_two_sfca_matches_official_two_sfca():
    # 거리 행렬 버전 2SFCA가 직선거리에서 공식 two_sfca()와 같아야 도로 비교가 공정하다
    import numpy as np

    from src.access.catchment import load_demand_points, load_supply_points
    from src.access.network import straight_distance_matrix, two_sfca_from_matrix
    from src.access.two_sfca import two_sfca

    demand, supply = load_demand_points(), load_supply_points("보건의료")
    ours = two_sfca_from_matrix(
        straight_distance_matrix(demand, supply),
        demand["pop_total"].to_numpy(dtype=float),
        supply["capacity"].to_numpy(dtype=float),
        3000,
    )
    official = two_sfca("보건의료", 3).set_index("adm_cd")["access_index"].loc[demand["adm_cd"]].to_numpy()
    assert np.allclose(ours, official)


@pytest.mark.skipif(not Path("data/raw/osm/pohang_drive.graphml").exists(), reason="OSM 도로망 없음")
def test_road_distance_is_never_shorter_than_straight():
    import numpy as np

    from src.access.network import compare_road_vs_straight

    result = compare_road_vs_straight()
    nearest = result["nearest"]
    # 도로로 가는 길이 직선보다 짧을 수는 없다(붙이는 거리까지 더하므로)
    assert (nearest["nearest_road_m"] >= nearest["nearest_straight_m"] - 1e-6).all()
    assert result["detour_quantiles"]["p50"] > 1
