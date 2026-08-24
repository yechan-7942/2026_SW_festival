from pathlib import Path

import pandas as pd

from src.ingest.datagokr import load_facilities
from src.preprocess.crs import reproject_points

LEGAL_DONG_MAP_PATH = "data/processed/legal_dong_to_admin.csv"
ADM_CODE_MAP_PATH = "data/processed/adm_code_map.csv"
INTERIM_FACILITIES_PATH = "data/interim/facilities_pohang.parquet"

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
    df = pd.read_csv(ADM_CODE_MAP_PATH)
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


def save_facilities_interim(path: str = INTERIM_FACILITIES_PATH) -> str:
    """조인+재투영된 시설 테이블을 data/interim/에 캐시한다.

    아직 capacity 컬럼이 없어(README §5 facilities.parquet 계약 미충족)
    data/processed/가 아니라 data/interim/에 둔다 — 이후 상세정보서비스
    파일에서 capacity를 derive하면 그때 data/processed/로 승격한다.
    """
    joined = join_facilities_to_admin_dong()
    reprojected = reproject_points(joined)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    reprojected.to_parquet(path)
    return path


def validate_point_in_polygon(joined: pd.DataFrame):
    """SGIS 행정동 경계 폴리곤 확보 후 구현할 2차 검증 자리표시자.

    reports/m1_structure_proposal.md 블로커 1(SGIS Open API 키)이 풀리면,
    joined의 (lon, lat)을 crs.reproject_points로 변환한 뒤 행정동 폴리곤과
    point-in-polygon 대조해 이름 매칭 결과와 불일치하는 레코드를 찾는다.
    """
    raise NotImplementedError("SGIS 행정동 경계 파일 확보 전까지는 구현할 수 없음")


if __name__ == "__main__":
    counts = facility_counts_by_dong()
    print(counts.to_string(index=False))
    print(f"\n총 시설 수: {counts['count'].sum()}")
