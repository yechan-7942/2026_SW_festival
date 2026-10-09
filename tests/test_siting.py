from pathlib import Path

import numpy as np
import pytest

PROCESSED_DATA_AVAILABLE = (
    Path("data/processed/admin_units.parquet").exists()
    and Path("data/processed/pop_centroids.parquet").exists()
    and Path("data/processed/facilities.parquet").exists()
)

pytestmark = pytest.mark.skipif(
    not PROCESSED_DATA_AVAILABLE,
    reason="data/processed 산출물이 없음 — 먼저 파이프라인을 실행해야 함",
)


@pytest.fixture(scope="module")
def candidates():
    from src.gap.siting import rank_candidates

    return rank_candidates()


def test_candidates_cover_every_dong_and_threshold(candidates):
    # 동 29개 × 임계거리 3개
    assert len(candidates) == 29 * 3
    assert candidates.groupby("threshold_km").size().eq(29).all()


def test_adding_a_facility_never_raises_the_gap_of_its_own_dong(candidates):
    # 공급을 더하면 자기 동의 접근성이 늘 수는 있어도 줄지는 않는다(분모 증가 효과는 R_j에만 영향)
    assert (candidates["own_gap_reduction"] >= -1e-12).all()


def test_foreign_weighted_gap_does_not_increase(candidates):
    # 정규화 기준을 기준선으로 고정했으므로 시설 추가가 외국인 가중 평균 격차를 올리지 않는다
    assert (candidates["foreign_gap_reduction"] >= -1e-9).all()


def test_no_site_changes_when_nothing_is_added():
    from src.gap.siting import simulate_sites

    result = simulate_sites([])
    assert np.allclose(result["gap_change"], 0)
    assert result["rank_before"].equals(result["rank_after"])


def test_simulation_matches_official_gap_score_before():
    # 시설을 더하기 전 gap_before는 공식 산출물(gap_scores.parquet)의 순위와 같아야 한다
    import pandas as pd

    from src.gap.siting import simulate_sites

    official = pd.read_parquet("data/processed/gap_scores.parquet").set_index("adm_cd")["rank"]
    ours = simulate_sites([]).set_index("adm_cd")["rank_before"]
    assert ours.sort_index().equals(official.sort_index())


def test_larger_capacity_helps_at_least_as_much():
    from src.gap.siting import simulate_sites

    first = simulate_sites(["15216A1100A37010B000J320"], capacity=3)
    second = simulate_sites(["15216A1100A37010B000J320"], capacity=9)
    assert (second["gap_after"] <= first["gap_after"] + 1e-12).all()


def test_unknown_site_is_rejected():
    from src.gap.siting import simulate_sites

    with pytest.raises(ValueError):
        simulate_sites(["없는코드"])


def test_greedy_plan_picks_distinct_sites_and_accumulates():
    from src.gap.siting import greedy_plan

    plan = greedy_plan(3)
    assert plan["adm_cd"].is_unique
    assert plan["cumulative_reduction"].is_monotonic_increasing
