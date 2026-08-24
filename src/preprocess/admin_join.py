from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.ingest.datagokr import load_facilities
from src.ingest.kosis import population_by_dong
from src.ingest.sgis import fetch_pohang_boundaries
from src.preprocess.crs import reproject_points

LEGAL_DONG_MAP_PATH = "data/processed/legal_dong_to_admin.csv"
ADM_CODE_MAP_PATH = "data/processed/adm_code_map.csv"
FACILITIES_OUTPUT_PATH = "data/processed/facilities.parquet"
ADMIN_UNITS_OUTPUT_PATH = "data/processed/admin_units.parquet"

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
    joined = join_facilities_to_admin_dong()
    return (
        joined.groupby(["adm_cd", "gu", "fac_type"])
        .size()
        .reset_index(name="count")
    )


def save_facilities(path: str = FACILITIES_OUTPUT_PATH) -> str:
    """조인+재투영된 시설 테이블을 README §5 facilities.parquet 계약대로 저장한다.

    계약이 요구하는 [fac_id, fac_type, lon, lat, capacity]에 legal_dong_nm,
    gu, adm_cd, geometry(EPSG:5179)를 덧붙인 상위 집합이다 — access 레이어가
    필요하면 쓰고 아니면 무시할 수 있다.
    """
    joined = join_facilities_to_admin_dong()
    reprojected = reproject_points(joined)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    reprojected.to_parquet(path)
    return path


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
