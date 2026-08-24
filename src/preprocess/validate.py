import pandas as pd

# 포항시 경계 상자. 908건 실측 좌표 범위(lon 129.02~129.56, lat 35.84~36.25 —
# 서쪽 죽장면, 남동쪽 장기면/호미곶면까지 포함)에 여유를 둔 값이다. 정밀 검증은
# SGIS 경계 확보 후 point-in-polygon으로 대체한다.
POHANG_LON_RANGE = (128.95, 129.65)
POHANG_LAT_RANGE = (35.80, 36.30)


def validate_admin_code_map(df: pd.DataFrame) -> list[str]:
    """adm_code_map.csv 자체 정합성 검증."""
    errors = []

    current = df[df["status"] == "current"]
    self_inconsistent = current[current["adm_cd_kosis"] != current["current_adm_cd_kosis"]]
    if len(self_inconsistent) > 0:
        errors.append(
            f"status=current인데 adm_cd_kosis != current_adm_cd_kosis인 행: "
            f"{self_inconsistent['adm_nm'].tolist()}"
        )

    current_codes = set(current["adm_cd_kosis"])
    dangling = df[~df["current_adm_cd_kosis"].isin(current_codes)]
    if len(dangling) > 0:
        errors.append(
            f"current_adm_cd_kosis가 존재하는 current 행을 가리키지 않는 행: "
            f"{dangling['adm_nm'].tolist()}"
        )

    bad_gu = df[~df["gu"].isin(["남구", "북구"])]
    if len(bad_gu) > 0:
        errors.append(f"gu 값이 남구/북구가 아닌 행: {bad_gu['adm_nm'].tolist()}")

    return errors


def validate_legal_dong_map(legal_map: pd.DataFrame, admin_units: pd.DataFrame) -> list[str]:
    """legal_dong_to_admin.csv의 admin_dong_nm이 실제 현재 행정동인지 검증."""
    errors = []
    valid_pairs = set(zip(admin_units["gu"], admin_units["adm_nm"]))
    joined_pairs = set(zip(legal_map["gu"], legal_map["admin_dong_nm"]))
    dangling = joined_pairs - valid_pairs
    if dangling:
        errors.append(f"adm_code_map.csv의 현재 행정동에 없는 (gu, admin_dong_nm): {sorted(dangling)}")
    return errors


def validate_facility_records(df: pd.DataFrame) -> list[str]:
    """시설 레코드의 결측·중복·좌표 이상치 검증."""
    errors = []

    if df["fac_id"].duplicated().any():
        dupes = df.loc[df["fac_id"].duplicated(), "fac_id"].tolist()
        errors.append(f"fac_id 중복 {len(dupes)}건")

    for col in ("adm_cd", "lon", "lat", "fac_type"):
        n_null = df[col].isna().sum()
        if n_null > 0:
            errors.append(f"{col} 결측 {n_null}건")

    out_of_bounds = df[
        ~df["lon"].between(*POHANG_LON_RANGE) | ~df["lat"].between(*POHANG_LAT_RANGE)
    ]
    if len(out_of_bounds) > 0:
        errors.append(
            f"포항 경계 상자({POHANG_LON_RANGE}, {POHANG_LAT_RANGE}) 밖 좌표 "
            f"{len(out_of_bounds)}건: {out_of_bounds['fac_id'].tolist()}"
        )

    return errors


def run_all() -> bool:
    from src.preprocess.admin_join import (
        join_facilities_to_admin_dong,
        load_current_admin_units,
        load_legal_dong_map,
    )

    adm_code_map = pd.read_csv("data/processed/adm_code_map.csv")
    legal_map = load_legal_dong_map()
    admin_units = load_current_admin_units()
    facilities = join_facilities_to_admin_dong()

    all_errors = {
        "adm_code_map.csv": validate_admin_code_map(adm_code_map),
        "legal_dong_to_admin.csv": validate_legal_dong_map(legal_map, admin_units),
        "facilities (admin_join 출력)": validate_facility_records(facilities),
    }

    ok = True
    for source, errors in all_errors.items():
        if errors:
            ok = False
            print(f"[FAIL] {source}")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"[OK] {source}")

    return ok


if __name__ == "__main__":
    import sys

    sys.exit(0 if run_all() else 1)
