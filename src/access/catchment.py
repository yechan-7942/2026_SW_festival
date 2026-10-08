import warnings
from pathlib import Path

import geopandas as gpd
import yaml

import pandas as pd

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
POP_CENTROIDS_PATH = "data/processed/pop_centroids.parquet"
FACILITIES_PATH = "data/processed/facilities.parquet"
DEFAULT_CONFIG_PATH = "config/pipeline.yaml"


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_demand_points(
    admin_units_path: str = ADMIN_UNITS_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
    pop_centroids_path: str = POP_CENTROIDS_PATH,
) -> gpd.GeoDataFrame:
    """행정동 수요점 — pop_total을 공급을 두고 경쟁하는 수요로 쓴다.

    위치는 config의 access.demand_point로 고른다.
    - population_weighted(기본): 집계구 인구로 가중평균한 중심점(src/ingest/sgis_tract.py).
      pop_centroids.parquet가 없으면 기하 중심점으로 대체하고 경고를 낸다.
    - geometric: 행정동 폴리곤의 기하 중심점. 죽장면·오천읍처럼 넓고 오목한 행정동은
      실제 인구 밀집 지역과 최대 수 km 어긋난다(오천읍 5.4km).
    """
    admin_units = gpd.read_parquet(admin_units_path)
    demand = admin_units[["adm_cd", "adm_nm", "pop_total", "pop_foreign"]].copy()
    mode = load_config(config_path).get("access", {}).get("demand_point", "population_weighted")
    points = admin_units.geometry.centroid

    if mode == "population_weighted":
        if Path(pop_centroids_path).exists():
            cent = pd.read_parquet(pop_centroids_path).set_index("adm_cd")
            has = admin_units["adm_cd"].isin(cent.index)
            x = points.x.where(~has, admin_units["adm_cd"].map(cent["x"]))
            y = points.y.where(~has, admin_units["adm_cd"].map(cent["y"]))
            points = gpd.points_from_xy(x, y)
        else:
            warnings.warn(f"{pop_centroids_path}가 없어 기하 중심점을 쓴다 — ingest 단계를 먼저 실행할 것", stacklevel=2)
    elif mode != "geometric":
        raise ValueError(f"알 수 없는 access.demand_point: {mode!r} (population_weighted | geometric)")

    demand = gpd.GeoDataFrame(demand, geometry=points, crs=admin_units.crs)
    return demand.reset_index(drop=True)


def load_supply_points(category_large: str, facilities_path: str = FACILITIES_PATH) -> gpd.GeoDataFrame:
    """category_large(대분류)로 필터링한 공급점 — capacity를 공급량으로 쓴다."""
    facilities = gpd.read_parquet(facilities_path)
    supply = facilities[facilities["category_large"] == category_large]
    return supply[["fac_id", "fac_type", "capacity", "geometry"]].reset_index(drop=True)
