import os
import yaml
import requests
import geopandas as gpd
from shapely.geometry import shape
from dotenv import load_dotenv

load_dotenv()

AUTH_URL = "https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json"
BOUNDARY_URL = "https://sgisapi.mods.go.kr/OpenAPI3/boundary/hadmarea.geojson"
DEFAULT_CONFIG_PATH = "config/pipeline.yaml"

# SGIS 경계 API는 좌표를 EPSG:5179(통계청 UTM-K)로 반환한다.
# (실측 확인: 포항 남구 표본 좌표 x=1186855, y=1783607 — 프로젝트 target_crs와 동일)
SGIS_CRS = "EPSG:5179"


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# SGIS는 consumer_key당 활성 토큰이 하나뿐이라, 호출마다 새로 발급하면 직전
# 토큰이 무효화되면서 동시/근접 호출이 서로를 깨뜨리는 경합이 생긴다(관측:
# 같은 프로세스에서 연달아 발급받은 토큰이 간헐적으로 "인증 정보가 존재하지
# 않습니다" 에러를 냄). 프로세스 수명 동안 하나의 토큰을 재사용하고, 실제로
# 만료/무효화된 경우에만 재발급한다.
_token_cache: dict[str, str] = {}


def get_access_token(force_refresh: bool = False) -> str:
    if not force_refresh and "token" in _token_cache:
        return _token_cache["token"]
    consumer_key = os.getenv("SGIS_CONSUMER_KEY")
    consumer_secret = os.getenv("SGIS_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        raise ValueError("SGIS_CONSUMER_KEY/SGIS_CONSUMER_SECRET이 .env에 설정되어 있지 않습니다.")
    response = requests.get(
        AUTH_URL,
        params={"consumer_key": consumer_key, "consumer_secret": consumer_secret},
    )
    data = response.json()
    if data.get("errCd") != 0:
        raise RuntimeError(f"SGIS 인증 실패: {data.get('errMsg')}")
    token = data["result"]["accessToken"]
    _token_cache["token"] = token
    return token


def _request_boundary(sigungu_cd: str, year: str, token: str) -> dict:
    response = requests.get(
        BOUNDARY_URL,
        params={
            "accessToken": token,
            "year": year,
            "adm_cd": sigungu_cd,
            "low_search": 1,
        },
    )
    return response.json()


def fetch_admin_boundaries(sigungu_codes: list[str], year: str, access_token: str | None = None) -> gpd.GeoDataFrame:
    token = access_token or get_access_token()
    records = []
    for sigungu_cd in sigungu_codes:
        data = _request_boundary(sigungu_cd, year, token)
        if data.get("errCd") != 0 and access_token is None:
            # 캐시된 토큰이 무효화됐을 가능성 — 정확한 에러코드를 신뢰하지 않고
            # 한 번만 강제 재발급 후 재시도한다(그래도 실패하면 아래에서 raise).
            token = get_access_token(force_refresh=True)
            data = _request_boundary(sigungu_cd, year, token)
        if data.get("errCd") != 0:
            raise RuntimeError(f"SGIS 경계 조회 실패(adm_cd={sigungu_cd}): {data.get('errMsg')}")
        for feature in data["features"]:
            props = feature["properties"]
            records.append(
                {
                    "adm_cd_sgis": props["adm_cd"],
                    "adm_nm_full": props["adm_nm"],
                    "adm_nm": props["adm_nm"].split()[-1],
                    "gu": props["adm_nm"].split()[-2],
                    "geometry": shape(feature["geometry"]),
                }
            )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=SGIS_CRS)


def fetch_pohang_boundaries(config_path: str = DEFAULT_CONFIG_PATH) -> gpd.GeoDataFrame:
    config = load_config(config_path)
    sgis_cfg = config["sgis"]
    return fetch_admin_boundaries(sgis_cfg["sigungu_codes"], sgis_cfg["year"])


if __name__ == "__main__":
    gdf = fetch_pohang_boundaries()
    print(gdf[["adm_cd_sgis", "gu", "adm_nm"]])
