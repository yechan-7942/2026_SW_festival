from pathlib import Path

import pytest

RAW_DATA_AVAILABLE = Path(
    "data/raw/전국 병의원 및 약국 현황 2026.3/1.병원정보서비스(2026.3.).xlsx"
).exists()

pytestmark = pytest.mark.skipif(
    not RAW_DATA_AVAILABLE,
    reason="data/raw/의 심평원 원본 파일이 없음 (gitignore 대상 — 로컬에 직접 받아야 함)",
)


def test_all_pohang_facilities_match_a_current_admin_dong():
    from src.preprocess.admin_join import join_facilities_to_admin_dong

    joined = join_facilities_to_admin_dong()
    assert joined["adm_cd"].isna().sum() == 0


def test_facility_count_matches_raw_pohang_subset():
    from src.ingest.datagokr import load_facilities
    from src.preprocess.admin_join import POHANG_SIGUNGU, join_facilities_to_admin_dong

    raw = load_facilities()
    expected = raw["sigungu_nm"].isin(POHANG_SIGUNGU).sum()

    joined = join_facilities_to_admin_dong()
    assert len(joined) == expected


def test_capacity_is_never_zero_or_negative():
    from src.preprocess.admin_join import join_facilities_to_admin_dong

    joined = join_facilities_to_admin_dong()
    assert (joined["capacity"] >= 1).all()
