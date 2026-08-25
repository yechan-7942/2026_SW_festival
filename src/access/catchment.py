import geopandas as gpd
import yaml

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
FACILITIES_PATH = "data/processed/facilities.parquet"
DEFAULT_CONFIG_PATH = "config/pipeline.yaml"


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_demand_points(admin_units_path: str = ADMIN_UNITS_PATH) -> gpd.GeoDataFrame:
    """행정동 수요점 — 폴리곤 중심점을 위치로, pop_total을 공급을 두고 경쟁하는 수요로 쓴다.

    죽장면처럼 크고 오목한 행정동은 중심점이 실제 인구 밀집 지역과 어긋날 수
    있다(프로토타입 단계의 알려진 단순화 — README "M2. 2SFCA 접근성 프로토타입").
    """
    admin_units = gpd.read_parquet(admin_units_path)
    demand = admin_units[["adm_cd", "adm_nm", "pop_total", "pop_foreign"]].copy()
    demand = gpd.GeoDataFrame(demand, geometry=admin_units.geometry.centroid, crs=admin_units.crs)
    return demand.reset_index(drop=True)


def load_supply_points(category_large: str, facilities_path: str = FACILITIES_PATH) -> gpd.GeoDataFrame:
    """category_large(대분류)로 필터링한 공급점 — capacity를 공급량으로 쓴다."""
    facilities = gpd.read_parquet(facilities_path)
    supply = facilities[facilities["category_large"] == category_large]
    return supply[["fac_id", "fac_type", "capacity", "geometry"]].reset_index(drop=True)
