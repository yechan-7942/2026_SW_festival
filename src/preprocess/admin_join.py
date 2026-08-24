from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.ingest.commercial import fetch_pohang_stores
from src.ingest.datagokr import load_facilities
from src.ingest.kosis import population_by_dong
from src.ingest.sgis import fetch_pohang_boundaries
from src.preprocess.crs import reproject_points

LEGAL_DONG_MAP_PATH = "data/processed/legal_dong_to_admin.csv"
ADM_CODE_MAP_PATH = "data/processed/adm_code_map.csv"
FACILITIES_OUTPUT_PATH = "data/processed/facilities.parquet"
ADMIN_UNITS_OUTPUT_PATH = "data/processed/admin_units.parquet"

# 의료·상가 두 소스를 합친 facilities.parquet의 최종 컬럼. README §5 계약
# [fac_id, fac_type, lon, lat, capacity]에 gu/adm_cd/geometry(공간 조인
# 결과)와 category_large(대분류 — fac_type 어휘가 소스마다 달라서 필요,
# 아래 load_commercial_facilities_with_admin_dong 참고)를 덧붙인다.
# legal_dong_nm은 심평원 데이터에만 있는 값이라 상가 쪽은 결측으로 둔다.
FACILITIES_COLUMNS = [
    "fac_id",
    "fac_type",
    "category_large",
    "legal_dong_nm",
    "lon",
    "lat",
    "capacity",
    "gu",
    "adm_cd",
    "geometry",
]

# 심평원 fac_type(종별코드명, 예: 종합병원·약국)과 상가정보 fac_type(업종중분류,
# 예: 편의점·일반의류 소매업)은 서로 다른 어휘 체계다. access 레이어가 "의료"
# 대분류로 묶어 필터링할 수 있도록, 상가정보가 이미 제외한 대분류명("보건의료",
# commercial.py의 EXCLUDED_CATEGORY_LARGE)을 그대로 재사용해 일관성을 맞춘다.
MEDICAL_CATEGORY_LARGE = "보건의료"

# 심평원 전국 데이터(src.ingest.datagokr)에서 포항만 골라내는 지역 필터.
# 원본 sigungu_nm 값 → 표준 gu명.
POHANG_SIGUNGU = {"포항남구": "남구", "포항북구": "북구"}


def load_pohang_facilities() -> pd.DataFrame:
    facilities = load_facilities()
    facilities = facilities[facilities["sigungu_nm"].isin(POHANG_SIGUNGU)].copy()
    facilities["gu"] = facilities["sigungu_nm"].map(POHANG_SIGUNGU)
    return facilities.drop(columns="sigungu_nm")


def load_legal_dong_map() -> pd.DataFrame:
    return pd.read_csv(LEGAL_DONG_MAP_PATH)


def load_current_admin_units() -> pd.DataFrame:
    df = pd.read_csv(ADM_CODE_MAP_PATH, dtype={"adm_cd_sgis": str})
    return df[df["status"] == "current"]


def join_facilities_to_admin_dong() -> pd.DataFrame:
    """1차 조인: 법정동 이름 매칭으로 심평원 시설 레코드에 행정동 코드를 붙인다.

    2차 검증(좌표 point-in-polygon 교차 확인, reports/m1_structure_proposal.md
    3번 항목)은 SGIS 행정동 경계 파일이 아직 없어 미구현 상태다. 이 함수의
    결과는 그때까지 이름 매칭만으로 얻은 잠정 결과로 취급해야 한다.
    """
    facilities = load_pohang_facilities()
    legal_map = load_legal_dong_map()
    admin_units = load_current_admin_units()

    merged = facilities.merge(legal_map, on=["gu", "legal_dong_nm"], how="left")
    unmatched = merged[merged["admin_dong_nm"].isna()]
    if len(unmatched) > 0:
        raise ValueError(
            f"{len(unmatched)}건의 시설 레코드가 legal_dong_to_admin.csv에 없는 "
            f"법정동입니다: {sorted(unmatched['legal_dong_nm'].unique().tolist())}"
        )

    merged = merged.merge(
        admin_units[["adm_nm", "gu", "current_adm_cd_kosis"]],
        left_on=["admin_dong_nm", "gu"],
        right_on=["adm_nm", "gu"],
        how="left",
    )
    merged = merged.rename(columns={"current_adm_cd_kosis": "adm_cd"})
    return merged.drop(columns=["admin_dong_nm", "adm_nm"])


def facility_counts_by_dong() -> pd.DataFrame:
    combined = build_facilities()
    return (
        combined.groupby(["adm_cd", "gu", "category_large", "fac_type"])
        .size()
        .reset_index(name="count")
    )


def load_admin_boundaries() -> gpd.GeoDataFrame:
    """SGIS 경계(adm_cd_sgis 기준)를 adm_code_map.csv 크로스워크로 adm_cd_kosis 기준으로 바꾼다."""
    boundaries = fetch_pohang_boundaries()
    crosswalk = load_current_admin_units()[["current_adm_cd_kosis", "adm_cd_sgis"]]
    merged = boundaries.merge(crosswalk, on="adm_cd_sgis", how="left")
    unmatched = merged[merged["current_adm_cd_kosis"].isna()]
    if len(unmatched) > 0:
        raise ValueError(
            f"{len(unmatched)}개 SGIS 행정동이 adm_code_map.csv와 매칭되지 않습니다: "
            f"{sorted(unmatched['adm_nm_full'].unique().tolist())}"
        )
    merged = merged.rename(columns={"current_adm_cd_kosis": "adm_cd"})
    return gpd.GeoDataFrame(merged[["adm_cd", "adm_nm", "gu", "geometry"]], geometry="geometry", crs=boundaries.crs)


def validate_point_in_polygon(joined: pd.DataFrame) -> pd.DataFrame:
    """법정동 이름 매칭(1차 조인)으로 배정된 adm_cd가 실제 좌표 기준 SGIS
    행정동 폴리곤과 일치하는지 point-in-polygon으로 교차검증한다.

    일치 여부만 판정하고 파이프라인을 중단시키지는 않는다 — 법정동/행정동
    괴리(reports/m1_legal_dong_mapping.md)처럼 실제 데이터 특성일 수 있어
    호출부가 결과를 보고 판단하게 한다. 반환값은 불일치 레코드만 담은
    DataFrame이며, 비어 있으면 전부 일치한다는 뜻이다.
    """
    points = reproject_points(joined)
    boundaries = load_admin_boundaries()
    sjoined = gpd.sjoin(points, boundaries[["adm_cd", "geometry"]], how="left", predicate="within")
    mismatched = sjoined[sjoined["adm_cd_left"] != sjoined["adm_cd_right"]]
    return mismatched[["fac_id", "adm_cd_left", "adm_cd_right"]].rename(
        columns={"adm_cd_left": "adm_cd_by_name", "adm_cd_right": "adm_cd_by_point"}
    )


def load_medical_facilities_with_admin_dong() -> gpd.GeoDataFrame:
    """심평원 시설(법정동 이름 매칭 + 재투영)에 category_large를 붙여 상가와 스키마를 맞춘다."""
    joined = join_facilities_to_admin_dong()
    reprojected = reproject_points(joined)
    reprojected["category_large"] = MEDICAL_CATEGORY_LARGE
    return reprojected


def load_commercial_facilities_with_admin_dong() -> gpd.GeoDataFrame:
    """상가업소(commercial.fetch_pohang_stores)에 SGIS 폴리곤 point-in-polygon으로 행정동을 배정한다.

    상가정보에는 법정동 텍스트가 없어(commercial.py 주석 참고) 심평원처럼
    이름 매칭을 할 수 없다 — 좌표 조인이 유일한 방법이다. inner join이라
    반경검색이 포항 경계 밖까지 끌어온 레코드(흥해읍 등 경계 인접 동 주변,
    reports/m2_commercial.md에 알려진 한계로 기록)는 여기서 자연히 제외된다:
    어떤 포항 행정동 폴리곤에도 속하지 않는 점이기 때문이다.
    """
    stores = fetch_pohang_stores()
    points = reproject_points(stores)
    boundaries = load_admin_boundaries()
    sjoined = gpd.sjoin(points, boundaries[["adm_cd", "gu", "geometry"]], how="inner", predicate="within")
    duplicated = sjoined[sjoined["fac_id"].duplicated(keep=False)]
    if len(duplicated) > 0:
        raise ValueError(f"{len(duplicated)}건의 상가 레코드가 행정동 폴리곤에 중복 배정됐습니다: {duplicated['fac_id'].tolist()[:5]}")
    sjoined = sjoined.drop(columns="index_right")
    sjoined["legal_dong_nm"] = pd.NA
    return sjoined


def build_facilities() -> gpd.GeoDataFrame:
    """README §5 facilities.parquet 계약: 의료·상가 두 소스를 하나의 [fac_id, fac_type, lon, lat, capacity] 테이블로 합친다."""
    medical = load_medical_facilities_with_admin_dong()[FACILITIES_COLUMNS]
    commercial = load_commercial_facilities_with_admin_dong()[FACILITIES_COLUMNS]
    combined = pd.concat([medical, commercial], ignore_index=True)
    duplicated = combined[combined["fac_id"].duplicated(keep=False)]
    if len(duplicated) > 0:
        raise ValueError(f"두 소스에서 fac_id가 겹칩니다: {duplicated['fac_id'].tolist()[:5]}")
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=medical.crs)


def save_facilities(path: str = FACILITIES_OUTPUT_PATH) -> str:
    """의료·상가를 합친 시설 테이블을 README §5 facilities.parquet 계약대로 저장한다.

    계약이 요구하는 [fac_id, fac_type, lon, lat, capacity]에 category_large,
    legal_dong_nm, gu, adm_cd, geometry(EPSG:5179)를 덧붙인 상위 집합이다 —
    access 레이어가 필요하면 쓰고 아니면 무시할 수 있다.
    """
    combined = build_facilities()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(path)
    return path


def build_admin_units() -> gpd.GeoDataFrame:
    """README §5 admin_units.parquet 계약: [adm_cd, adm_nm, geometry, pop_total, pop_foreign]."""
    boundaries = load_admin_boundaries()
    population = population_by_dong()
    merged = boundaries.merge(population, on="adm_cd", how="left")
    unmatched = merged[merged["pop_total"].isna()]
    if len(unmatched) > 0:
        raise ValueError(f"{len(unmatched)}개 행정동에 인구 데이터가 없습니다: {sorted(unmatched['adm_nm'].unique().tolist())}")
    return gpd.GeoDataFrame(
        merged[["adm_cd", "adm_nm", "geometry", "pop_total", "pop_foreign"]],
        geometry="geometry",
        crs=boundaries.crs,
    )


def save_admin_units(path: str = ADMIN_UNITS_OUTPUT_PATH) -> str:
    gdf = build_admin_units()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(path)
    return path


if __name__ == "__main__":
    counts = facility_counts_by_dong()
    print(counts.to_string(index=False))
    print(f"\n총 시설 수: {counts['count'].sum()}")
