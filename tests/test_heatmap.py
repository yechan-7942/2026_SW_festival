from pathlib import Path

import plotly.graph_objects as go
import pytest

PROCESSED_DATA_AVAILABLE = (
    Path("data/processed/admin_units.parquet").exists() and Path("data/processed/gap_scores.parquet").exists()
)

pytestmark = pytest.mark.skipif(
    not PROCESSED_DATA_AVAILABLE,
    reason="data/processed/admin_units.parquet 또는 gap_scores.parquet가 없음 — 먼저 gap 파이프라인을 실행해야 함",
)


def test_load_geo_gap_scores_covers_all_29_dong():
    from src.viz.heatmap import load_geo_gap_scores

    gdf = load_geo_gap_scores("보건의료")
    assert len(gdf) == 29
    assert gdf["adm_cd"].is_unique
    assert set(gdf.columns) >= {"adm_cd", "adm_nm", "geometry", "gap_score", "rank", "cluster_id"}


def test_load_geo_gap_scores_raises_for_unknown_fac_type():
    from src.viz.heatmap import load_geo_gap_scores

    with pytest.raises(ValueError):
        load_geo_gap_scores("존재하지않는유형")


def test_build_gap_heatmap_returns_figure_with_choropleth_trace():
    from src.viz.heatmap import build_gap_heatmap

    fig = build_gap_heatmap("보건의료")
    assert isinstance(fig, go.Figure)
    trace_types = {trace.type for trace in fig.data}
    assert "choroplethmap" in trace_types
    choropleth = next(t for t in fig.data if t.type == "choroplethmap")
    assert len(choropleth.locations) == 29


def test_build_gap_ranking_bar_sorted_ascending_for_horizontal_display():
    from src.viz.heatmap import build_gap_ranking_bar

    fig = build_gap_ranking_bar("보건의료", top_n=10)
    assert isinstance(fig, go.Figure)
    bar = fig.data[0]
    assert len(bar.x) == 10
    # 가로 막대그래프는 화면상 위쪽이 1위가 되도록 오름차순으로 쌓는다.
    assert list(bar.x) == sorted(bar.x)


def test_save_figures_writes_png_and_html(tmp_path):
    from src.viz.heatmap import save_figures

    output_dir = str(tmp_path / "figures")
    paths = save_figures("보건의료", output_dir=output_dir)
    assert len(paths) == 4
    for path in paths:
        assert Path(path).exists()
        assert Path(path).stat().st_size > 0
