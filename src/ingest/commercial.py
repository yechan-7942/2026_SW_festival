import os
import urllib.parse

import geopandas as gpd
import pandas as pd
import requests
import yaml
from dotenv import load_dotenv
from shapely.geometry import Point

from src.ingest.sgis import fetch_pohang_boundaries

load_dotenv()

STORE_RADIUS_URL = "http://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"
DEFAULT_CONFIG_PATH = "config/pipeline.yaml"

# 응답 컬럼(영문) → 프로젝트 표준 컬럼명(README §5 facilities.parquet 계약과
# 맞춤: fac_id, fac_type, lon, lat). 행정동/법정동 관련 필드(adongCd 등)는
# 이 API 자체가 매기는 값이라 여기서 버린다 — 소속 행정동은 preprocess에서
# SGIS 폴리곤 기준 point-in-polygon으로 배정한다(법정동 텍스트가 없어 이름
# 매칭이 불가능하므로, datagokr.py의 의료기관과 달리 좌표 조인만 가능).
# fac_type은 중분류(indsMclsNm)로 둔다 — 의료기관 fac_type(종별코드명, 약
# 10~15종)과 비슷한 세분화 수준이면서, 대분류(indsLclsNm)보다 세밀해 access
# 레이어가 "금융" 등 특정 업종만 골라 쓰기 좋다. 대/소분류도 함께 남겨 둔다.
COLUMNS = {
    "bizesId": "fac_id",
    "bizesNm": "biz_nm",
    "indsLclsNm": "category_large",
    "indsMclsNm": "fac_type",
    "indsSclsNm": "category_small",
    "lon": "lon",
    "lat": "lat",
}

# 심평원 병의원 데이터(datagokr.py)가 의료기관을 이미 전담한다. 상가정보에도
# "보건의료" 대분류(병의원·약국 포함)가 섞여 있어 그대로 합치면 같은 시설이
# 두 소스에서 중복 집계되고, capacity 산정 기준(총의사수 vs 없음→1)도 서로
# 달라 access 레이어 계산이 왜곡된다. 그래서 이 대분류는 원천 제외한다.
EXCLUDED_CATEGORY_LARGE = {"보건의료"}

# 이 API는 종업원 수·매출·면적 등 규모 지표를 제공하지 않는다. datagokr.py의
# capacity floor 관례(0/NaN → 1)와 일관되게, 상가업소 1곳당 최소 공급 단위
# 1로 취급한다.
MIN_CAPACITY = 1


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_api_key() -> str:
    raw = os.getenv("DATA_GO_KR_API_KEY")
    if not raw:
        raise ValueError("DATA_GO_KR_API_KEY가 .env에 설정되어 있지 않습니다.")
    # data.go.kr 서비스키는 이미 URL-인코딩된 값으로 발급된다. requests가 params
    # 인코딩 시 다시 인코딩(이중 인코딩)하면 인증이 깨지므로 여기서 먼저 디코딩한다.
    return urllib.parse.unquote(raw)


def fetch_stores_in_radius(cx: float, cy: float, radius_m: float, num_of_rows: int = 1000) -> pd.DataFrame:
    """cx,cy(경도,위도) 중심 반경(m) 내 상가업소를 페이지네이션해서 전부 가져온다."""
    api_key = _get_api_key()
    page_no = 1
    rows = []
    while True:
        response = requests.get(
            STORE_RADIUS_URL,
            params={
                "serviceKey": api_key,
                "cx": cx,
                "cy": cy,
                "radius": int(radius_m),
                "numOfRows": num_of_rows,
                "pageNo": page_no,
                "type": "json",
            },
            timeout=30,
        )
        data = response.json()
        header = data["header"]
        if header["resultCode"] == "03":  # NODATA_ERROR — 해당 반경 내 결과 없음
            break
        if header["resultCode"] != "00":
            raise RuntimeError(f"상가정보 API 요청 실패: {header['resultCode']} {header['resultMsg']}")
        body = data["body"]
        rows.extend(body["items"])
        total_count = int(body["totalCount"])
        if len(rows) >= total_count:
            break
        page_no += 1
    output_columns = list(COLUMNS.values()) + ["capacity"]
    if not rows:
        return pd.DataFrame(columns=output_columns)
    df = pd.DataFrame(rows)
    df = df.rename(columns=COLUMNS)[list(COLUMNS.values())]
    df = df[~df["category_large"].isin(EXCLUDED_CATEGORY_LARGE)].copy()
    df["capacity"] = MIN_CAPACITY
    return df[output_columns]


def _dong_query_points(config_path: str = DEFAULT_CONFIG_PATH) -> gpd.GeoDataFrame:
    """행정동별 (중심좌표, 반경) — 중심→최원거리 정점 실측값 * margin, max_radius_m으로 상한."""
    config = load_config(config_path)
    commercial_cfg = config["commercial"]
    boundaries = fetch_pohang_boundaries(config_path)  # EPSG:5179(투영), 거리 계산은 이 좌표계에서 해야 정확

    def max_vertex_distance(geom) -> float:
        centroid = geom.centroid
        polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        vertex_distances = [
            centroid.distance(Point(x, y)) for poly in polygons for x, y in poly.exterior.coords
        ]
        return max(vertex_distances)

    boundaries = boundaries.copy()
    boundaries["radius_m"] = boundaries.geometry.apply(
        lambda g: min(max_vertex_distance(g) * commercial_cfg["radius_margin"], commercial_cfg["max_radius_m"])
    )
    boundaries["centroid"] = boundaries.geometry.centroid
    points = gpd.GeoDataFrame(
        boundaries[["adm_cd_sgis", "adm_nm", "gu", "radius_m"]],
        geometry=boundaries["centroid"],
        crs=boundaries.crs,
    ).to_crs("EPSG:4326")
    return points


def fetch_pohang_stores(config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """포항 29개 행정동 중심점마다 반경검색해 합치고 상가업소번호(fac_id) 기준 중복 제거.

    반경은 행정동별로 다르게 계산한다(중심→최원거리 정점 * margin, 10km 상한) —
    도심 소형 동에 일괄 10km를 쓰면 서로 겹쳐 같은 상가를 수십 번 중복 조회하게
    되기 때문. 흥해읍(11.0km)·죽장면(18.6km)은 실제 반경이 상한을 넘어 완전한
    커버리지가 아니다 — 알려진 한계로 reports/m2_commercial.md에 기록.
    """
    commercial_cfg = load_config(config_path)["commercial"]
    points = _dong_query_points(config_path)

    frames = []
    for _, row in points.iterrows():
        frames.append(
            fetch_stores_in_radius(
                row.geometry.x,
                row.geometry.y,
                row["radius_m"],
                commercial_cfg["num_of_rows_per_page"],
            )
        )
    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates(subset="fac_id").reset_index(drop=True)


if __name__ == "__main__":
    df = fetch_pohang_stores()
    print(df.head())
    print(f"\n총 상가업소 수(중복 제거): {len(df)}")
