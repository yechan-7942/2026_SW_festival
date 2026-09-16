from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
GAP_SCORES_PATH = "data/processed/gap_scores.parquet"
FIGURES_DIR = "outputs/figures"

# dataviz 스킬 팔레트(references/palette.md)의 sequential blue 램프. gap_score는
# 극성(polarity)이 아니라 단일 크기(magnitude, 0~1)라서 빨강/초록 발산(diverging)
# 배색이 아니라 단일 색조 sequential 램프를 쓴다 — README 개념설계서의 "빨강=취약,
# 초록=양호"는 색약 사용자에게 구분이 어려운 조합이라 채택하지 않았다.
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
BAR_HUE = "#2a78d6"  # 팔레트 categorical slot 1 — 랭킹 막대는 단일 계열이라 색조 하나로 충분


def load_geo_gap_scores(fac_type: str = "보건의료", gap_scores_path: str = GAP_SCORES_PATH) -> gpd.GeoDataFrame:
    """gap_scores.parquet + admin_units.parquet(geometry)를 합쳐 지도 표시용 GeoDataFrame을 만든다.

    plotly의 choropleth_map(MapLibre 기반, 토큰 불필요)은 위경도(EPSG:4326)
    GeoJSON을 요구하므로 admin_units.parquet의 좌표계(EPSG:5179)를 재투영한다.
    """
    admin_units = gpd.read_parquet(ADMIN_UNITS_PATH)[["adm_cd", "adm_nm", "geometry"]].to_crs(4326)
    gap_scores = pd.read_parquet(gap_scores_path)
    gap_scores = gap_scores[gap_scores["fac_type"] == fac_type]
    if len(gap_scores) == 0:
        raise ValueError(f"gap_scores.parquet에 fac_type='{fac_type}' 데이터가 없습니다.")

    merged = admin_units.merge(gap_scores, on="adm_cd", how="inner")
    if len(merged) != len(admin_units):
        missing = set(admin_units["adm_cd"]) - set(merged["adm_cd"])
        raise ValueError(f"gap_scores.parquet에 없는 admin_units adm_cd: {missing}")
    return merged


def build_gap_heatmap(fac_type: str = "보건의료") -> go.Figure:
    """행정동별 격차 점수 코로플레스 지도.

    색은 sequential(단일 색조, 연할수록 격차 작음/진할수록 격차 큼)로만 인코딩한다
    — 격차 점수는 0(양호)~1(심각) 사이의 크기(magnitude)값이지, 기준점을 중심으로
    양쪽으로 갈리는 값(polarity)이 아니므로 diverging 배색은 데이터의 성격과
    맞지 않는다(dataviz 스킬 references/choosing-a-form.md).
    """
    gdf = load_geo_gap_scores(fac_type)
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": adm_cd, "geometry": geom.__geo_interface__}
            for adm_cd, geom in zip(gdf["adm_cd"], gdf.geometry)
        ],
    }
    min_lon, min_lat, max_lon, max_lat = gdf.total_bounds
    lon_pad = (max_lon - min_lon) * 0.04
    lat_pad = (max_lat - min_lat) * 0.04
    min_lon, max_lon = min_lon - lon_pad, max_lon + lon_pad
    min_lat, max_lat = min_lat - lat_pad, max_lat + lat_pad

    fig = px.choropleth_map(
        gdf,
        geojson=geojson,
        locations="adm_cd",
        color="gap_score",
        color_continuous_scale=SEQUENTIAL_BLUE,
        range_color=(0, 1),
        hover_name="adm_nm",
        hover_data={"adm_cd": False, "gap_score": ":.3f", "rank": True, "cluster_id": True},
        opacity=0.9,
        labels={"gap_score": "격차 점수", "rank": "순위", "cluster_id": "우선순위 구간"},
    )

    # carto-positron/OSM 타일은 이 지역 지명을 로마자로 표기해 한국어 보고서에
    # 어색하다. 베이스맵을 흰 배경으로 비우고, 행정동 이름은 폴리곤 내부의
    # 대표점(centroid 대신 representative_point — 죽장면처럼 오목한 도형도
    # 폴리곤 밖으로 라벨이 튀지 않음)에 직접 한글로 얹는다.
    label_points = gdf.geometry.representative_point()
    fig.add_scattermap(
        lat=label_points.y,
        lon=label_points.x,
        mode="text",
        text=gdf["adm_nm"],
        textfont=dict(size=10, color="#0b0b0b", family="system-ui, -apple-system, sans-serif"),
        hoverinfo="skip",
        showlegend=False,
    )

    fig.update_layout(
        map_style="white-bg",
        # zoom/center를 어림잡는 대신 29개 행정동 경계 bounds에 정확히 맞춘다.
        map_bounds={"west": min_lon, "east": max_lon, "south": min_lat, "north": max_lat},
        margin=dict(l=0, r=0, t=40, b=0),
        title=f"포항시 행정동별 {fac_type} 접근성 격차 점수 (2026, 임계거리 3km)",
        font=dict(family="system-ui, -apple-system, sans-serif"),
    )
    return fig


def build_gap_ranking_bar(fac_type: str = "보건의료", top_n: int = 15) -> go.Figure:
    """격차 점수 상위 N개 행정동 랭킹 막대그래프 — 지도만으로는 읽기 어려운 정확한 순위 비교용."""
    gdf = load_geo_gap_scores(fac_type)
    top = gdf.nlargest(top_n, "gap_score").sort_values("gap_score")

    fig = go.Figure(
        go.Bar(
            x=top["gap_score"],
            y=top["adm_nm"],
            orientation="h",
            marker_color=BAR_HUE,
            text=top["gap_score"].round(3),
            textposition="outside",
        )
    )
    fig.update_layout(
        title=f"{fac_type} 격차 점수 상위 {top_n}개 행정동 (우선 개선 후보)",
        xaxis_title="격차 점수",
        yaxis_title=None,
        margin=dict(l=10, r=40, t=40, b=10),
        font=dict(family="system-ui, -apple-system, sans-serif"),
        xaxis=dict(range=[0, 1.05]),
    )
    return fig


def save_figures(fac_type: str = "보건의료", output_dir: str = FIGURES_DIR) -> list[str]:
    """히트맵·랭킹 막대를 PNG(연구보고서 삽입용)와 HTML(대화형 탐색용)로 저장."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    saved = []

    heatmap = build_gap_heatmap(fac_type)
    bar = build_gap_ranking_bar(fac_type)

    for name, fig in [("gap_heatmap", heatmap), ("gap_ranking_bar", bar)]:
        png_path = f"{output_dir}/{name}.png"
        html_path = f"{output_dir}/{name}.html"
        fig.write_image(png_path, width=1400, height=1000, scale=2)
        # plotly.js를 파일에 통째로 넣지 않고 CDN 참조로 저장 — 안 그러면 파일당
        # 4~5MB가 되어 대화형 미리보기용치고 리포지토리에 커밋하기엔 과하다.
        fig.write_html(html_path, include_plotlyjs="cdn")
        saved.extend([png_path, html_path])

    return saved


if __name__ == "__main__":
    paths = save_figures()
    for path in paths:
        print(f"저장됨: {path}")
