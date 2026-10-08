from pathlib import Path

import pandas as pd
import pytest


def test_weighted_centroid_pulls_toward_populous_tract():
    from src.ingest.sgis_tract import weighted_centroid

    tracts = pd.DataFrame({"x": [0.0, 100.0], "y": [0.0, 0.0], "pop": [900.0, 100.0]})
    assert weighted_centroid(tracts) == (10.0, 0.0)


def test_weighted_centroid_returns_none_when_population_is_zero():
    from src.ingest.sgis_tract import weighted_centroid

    assert weighted_centroid(pd.DataFrame({"x": [1.0], "y": [1.0], "pop": [0.0]})) is None


@pytest.mark.skipif(
    not (Path("data/processed/admin_units.parquet").exists() and Path("data/processed/pop_centroids.parquet").exists()),
    reason="admin_units/pop_centroids.parquet 없음 — ingest 단계 필요",
)
def test_load_demand_points_modes_differ_and_pop_centroids_cover_all_dong(tmp_path):
    from src.access.catchment import load_demand_points

    cfg = tmp_path / "geo.yaml"
    cfg.write_text("access:\n  demand_point: geometric\n", encoding="utf-8")
    geo = load_demand_points(config_path=str(cfg))
    pw = load_demand_points()
    assert len(geo) == len(pw) == 29
    shift = (geo.geometry.distance(pw.geometry))
    assert shift.max() > 1000  # 오천읍 등은 수 km 이동
    assert shift.min() >= 0


def test_unknown_demand_point_mode_raises(tmp_path):
    from src.access.catchment import load_demand_points

    cfg = tmp_path / "bad.yaml"
    cfg.write_text("access:\n  demand_point: nonsense\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_demand_points(config_path=str(cfg))
