import pandas as pd

from src.preprocess.validate import (
    validate_admin_code_map,
    validate_facility_records,
    validate_legal_dong_map,
)


def _admin_code_row(**overrides):
    row = {
        "adm_cd_kosis": "A1",
        "adm_nm": "테스트동",
        "gu": "남구",
        "status": "current",
        "current_adm_cd_kosis": "A1",
        "current_adm_nm": "테스트동",
    }
    row.update(overrides)
    return row


def test_validate_admin_code_map_accepts_clean_data():
    df = pd.DataFrame([_admin_code_row()])
    assert validate_admin_code_map(df) == []


def test_validate_admin_code_map_flags_self_inconsistent_current_row():
    # current_adm_cd_kosis="A2"가 실제 존재하는 current 행을 가리키게 해서
    # (dangling-reference 체크는 통과) 자기참조 불일치만 단독으로 걸리게 한다.
    df = pd.DataFrame(
        [
            _admin_code_row(adm_cd_kosis="A1", current_adm_cd_kosis="A2"),
            _admin_code_row(adm_cd_kosis="A2", adm_nm="다른동", current_adm_cd_kosis="A2"),
        ]
    )
    errors = validate_admin_code_map(df)
    assert len(errors) == 1
    assert "테스트동" in errors[0]


def test_validate_admin_code_map_flags_dangling_reference():
    df = pd.DataFrame(
        [_admin_code_row(status="deprecated", adm_cd_kosis="A0", current_adm_cd_kosis="DOES_NOT_EXIST")]
    )
    errors = validate_admin_code_map(df)
    assert any("가리키지 않는" in e for e in errors)


def test_validate_admin_code_map_flags_bad_gu():
    df = pd.DataFrame([_admin_code_row(gu="동구")])
    errors = validate_admin_code_map(df)
    assert any("gu" in e for e in errors)


def test_validate_legal_dong_map_flags_unknown_admin_dong():
    admin_units = pd.DataFrame([{"gu": "남구", "adm_nm": "테스트동"}])
    legal_map = pd.DataFrame([{"gu": "남구", "legal_dong_nm": "테스트법정동", "admin_dong_nm": "없는동"}])
    errors = validate_legal_dong_map(legal_map, admin_units)
    assert len(errors) == 1


def test_validate_legal_dong_map_accepts_known_admin_dong():
    admin_units = pd.DataFrame([{"gu": "남구", "adm_nm": "테스트동"}])
    legal_map = pd.DataFrame([{"gu": "남구", "legal_dong_nm": "테스트법정동", "admin_dong_nm": "테스트동"}])
    assert validate_legal_dong_map(legal_map, admin_units) == []


def _facility_row(**overrides):
    row = {
        "fac_id": "F1",
        "fac_type": "의원",
        "adm_cd": "A1",
        "lon": 129.36,
        "lat": 36.02,
        "capacity": 1,
    }
    row.update(overrides)
    return row


def test_validate_facility_records_accepts_clean_data():
    df = pd.DataFrame([_facility_row(fac_id="F1"), _facility_row(fac_id="F2")])
    assert validate_facility_records(df) == []


def test_validate_facility_records_flags_duplicate_fac_id():
    df = pd.DataFrame([_facility_row(fac_id="F1"), _facility_row(fac_id="F1")])
    errors = validate_facility_records(df)
    assert any("중복" in e for e in errors)


def test_validate_facility_records_flags_null_capacity():
    df = pd.DataFrame([_facility_row(capacity=None)])
    errors = validate_facility_records(df)
    assert any("capacity 결측" in e for e in errors)


def test_validate_facility_records_flags_non_positive_capacity():
    df = pd.DataFrame([_facility_row(capacity=0)])
    errors = validate_facility_records(df)
    assert any("capacity가 0 이하" in e for e in errors)


def test_validate_facility_records_flags_out_of_bounds_coordinate():
    df = pd.DataFrame([_facility_row(lon=127.0, lat=37.5)])  # 서울 근방 — 포항 밖
    errors = validate_facility_records(df)
    assert any("경계 상자" in e for e in errors)
