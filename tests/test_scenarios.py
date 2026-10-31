"""정책 목적·공급 단위 변경이 실제 결과에 반영되는지 확인한다."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from src.gap.scenarios import build_comparison_payload, scenario_scores


@pytest.fixture
def two_towns():
    demand = gpd.GeoDataFrame(
        {"adm_cd": ["A", "B"], "adm_nm": ["집중지역", "인구지역"],
         "pop_total": [100, 10000], "pop_foreign": [20, 100]},
        geometry=[Point(0, 0), Point(10000, 0)], crs="EPSG:5179",
    )
    # 각 지역 주민당 공급을 같게 두어 수요 지표 선택의 효과만 분리한다.
    supply = gpd.GeoDataFrame({"capacity": [100., 10000.]},
                             geometry=[Point(0, 0), Point(10000, 0)], crs="EPSG:5179")
    return demand, supply


def test_policy_purpose_changes_priority_at_equal_per_capita_supply(two_towns):
    demand, supply = two_towns
    ratio = scenario_scores(demand, supply, 1, "ratio")
    count = scenario_scores(demand, supply, 1, "count")
    assert ratio.iloc[0].adm_cd == "A"
    assert count.iloc[0].adm_cd == "B"
    assert ratio.set_index("adm_cd").access_index.equals(count.set_index("adm_cd").access_index.reindex(["A", "B"]))


def test_capacity_unit_rescaling_changes_raw_access_not_normalized_priority(two_towns):
    demand, supply = two_towns
    baseline = scenario_scores(demand, supply, 1, "ratio").set_index("adm_cd")
    scaled = scenario_scores(demand, supply.assign(capacity=supply.capacity * 3), 1, "ratio").set_index("adm_cd")
    np.testing.assert_allclose(scaled.access_index, baseline.access_index * 3)
    np.testing.assert_allclose(scaled.gap_score, baseline.gap_score)


def test_invalid_metric_is_not_silently_treated_as_ratio(two_towns):
    with pytest.raises(ValueError, match="수요 지표"):
        scenario_scores(*two_towns, 1, "typo")


@pytest.mark.parametrize("bad", [float('nan'), float('inf'), -1])
def test_invalid_supply_is_rejected_before_ranking(two_towns, bad):
    demand, supply = two_towns
    supply.loc[0, 'capacity'] = bad
    with pytest.raises(ValueError, match="공급"):
        scenario_scores(demand, supply, 1, "ratio")


def test_snapshot_keeps_real_zero_instead_of_imputing_one(tmp_path, monkeypatch, two_towns):
    from src.gap import scenarios
    demand, supply = two_towns
    del demand
    supply["fac_id"] = ["zero", "active"]
    supply["fac_type"] = ["보건진료소", "의원"]
    raw_path = tmp_path / "source.xlsx"
    pd.DataFrame({"암호화요양기호": ["zero", "active"], "총의사수": [0, 2]}).to_excel(raw_path, index=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scenarios, "load_supply_points", lambda _: supply)
    path = scenarios.prepare_practitioner_snapshot(str(raw_path))
    snapshot = pd.read_parquet(path).set_index("fac_id")
    assert snapshot.loc["zero", "recorded_practitioners"] == 0
    assert snapshot.recorded_practitioners.sum() == 2


def test_real_baseline_is_preserved_and_scenarios_use_same_whole_population():
    payload = build_comparison_payload()
    baseline = next(c for c in payload["cases"] if c["supply"] == "mixed" and c["metric"] == "ratio" and c["threshold_km"] == 3)
    reference = pd.read_parquet("data/processed/gap_scores.parquet").set_index("adm_cd")
    calculated = pd.DataFrame(baseline["rows"]).set_index("adm_cd")
    np.testing.assert_allclose(calculated.gap_score, reference.loc[calculated.index].gap_score, atol=1e-12)
    for case in payload["cases"]:
        frame = pd.DataFrame(case["rows"])
        assert len(frame) == 29 and frame.adm_cd.is_unique
        assert frame.pop_foreign.sum() == 7946
        assert frame.pop_total.sum() == 496653
        assert frame.gap_score.between(0, 1).all()
    assert payload["supplies"]["practitioners"]["capacity"] == 1323
    assert payload["supplies"]["pharmacies"]["capacity"] == 237


def test_real_purpose_comparison_does_not_reuse_ratio_ranks():
    payload = build_comparison_payload()
    case = next(c for c in payload["cases"] if c["supply"] == "mixed" and c["metric"] == "count" and c["threshold_km"] == 3)
    assert [r["adm_nm"] for r in case["rows"][:5]] == ["흥해읍", "구룡포읍", "오천읍", "장량동", "장기면"]
