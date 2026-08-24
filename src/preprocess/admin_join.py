import pandas as pd

HOSPITAL_PATH = "data/raw/전국 병의원 및 약국 현황 2026.3/1.병원정보서비스(2026.3.).xlsx"
PHARMACY_PATH = "data/raw/전국 병의원 및 약국 현황 2026.3/2.약국정보서비스(2026.3.).xlsx"
LEGAL_DONG_MAP_PATH = "data/processed/legal_dong_to_admin.csv"
ADM_CODE_MAP_PATH = "data/processed/adm_code_map.csv"

# 심평원 원본 컬럼명 → 파이프라인 표준 컬럼명.
# 주의: 원본 '읍면동' 컬럼은 행정동이 아니라 법정동 단위다 (예: 남빈동·대신동·
# 대흥동은 전부 중앙동 관할 법정동). reports/m1_legal_dong_mapping.md 참고.
FACILITY_COLUMNS = {
    "암호화요양기호": "fac_id",
    "종별코드명": "fac_type",
    "읍면동": "legal_dong_nm",
    "시군구코드명": "sigungu_nm",
    "좌표(X)": "lon",
    "좌표(Y)": "lat",
}
POHANG_SIGUNGU = {"포항남구": "남구", "포항북구": "북구"}


def load_pohang_facilities() -> pd.DataFrame:
    hospitals = pd.read_excel(HOSPITAL_PATH)
    pharmacies = pd.read_excel(PHARMACY_PATH)
    combined = pd.concat([hospitals, pharmacies], ignore_index=True)
    combined = combined[combined["시군구코드명"].isin(POHANG_SIGUNGU)]
    combined = combined.rename(columns=FACILITY_COLUMNS)[list(FACILITY_COLUMNS.values())]
    combined["gu"] = combined["sigungu_nm"].map(POHANG_SIGUNGU)
    return combined.drop(columns="sigungu_nm")


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
