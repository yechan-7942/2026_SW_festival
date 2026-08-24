import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

RAW_DATA_AVAILABLE = Path(
    "data/raw/전국 병의원 및 약국 현황 2026.3/1.병원정보서비스(2026.3.).xlsx"
).exists()
KEYS_AVAILABLE = all(
    os.getenv(k) for k in ("SGIS_CONSUMER_KEY", "SGIS_CONSUMER_SECRET", "KOSIS_API_KEY", "DATA_GO_KR_API_KEY")
)

pytestmark = pytest.mark.skipif(
    not (RAW_DATA_AVAILABLE and KEYS_AVAILABLE),
    reason="원본 심평원 파일 또는 SGIS/KOSIS/data.go.kr API 키가 로컬에 없음 — 전부 gitignore 대상",
)


def test_build_facilities_has_contract_columns_and_unique_fac_id():
    from src.preprocess.admin_join import FACILITIES_COLUMNS, build_facilities

    gdf = build_facilities()
    assert list(gdf.columns) == FACILITIES_COLUMNS
    assert gdf["fac_id"].is_unique
    assert gdf["adm_cd"].isna().sum() == 0


def test_build_facilities_combines_both_sources_without_double_counting_medical():
    from src.preprocess.admin_join import (
        MEDICAL_CATEGORY_LARGE,
        build_facilities,
        join_facilities_to_admin_dong,
    )

    gdf = build_facilities()
    medical_count = len(join_facilities_to_admin_dong())

    medical_rows = gdf[gdf["category_large"] == MEDICAL_CATEGORY_LARGE]
    commercial_rows = gdf[gdf["category_large"] != MEDICAL_CATEGORY_LARGE]

    assert len(medical_rows) == medical_count
    assert len(commercial_rows) > 0
    # 상가정보 쪽 보건의료 대분류는 commercial.py에서 이미 제외됨 — 심평원과 중복 집계 방지
    assert (commercial_rows["category_large"] != MEDICAL_CATEGORY_LARGE).all()


def test_build_facilities_capacity_is_never_zero_or_negative():
    from src.preprocess.admin_join import build_facilities

    gdf = build_facilities()
    assert (gdf["capacity"] >= 1).all()
