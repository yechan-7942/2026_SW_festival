import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

RAW_DATA_AVAILABLE = Path(
    "data/raw/전국 병의원 및 약국 현황 2026.3/1.병원정보서비스(2026.3.).xlsx"
).exists()
KEYS_AVAILABLE = all(
    os.getenv(k) for k in ("SGIS_CONSUMER_KEY", "SGIS_CONSUMER_SECRET", "KOSIS_API_KEY")
)

pytestmark = pytest.mark.skipif(
    not (RAW_DATA_AVAILABLE and KEYS_AVAILABLE),
    reason="원본 심평원 파일 또는 SGIS/KOSIS API 키가 로컬에 없음 — 둘 다 gitignore 대상",
)


def test_build_admin_units_has_29_rows_with_contract_columns():
    from src.preprocess.admin_join import build_admin_units

    gdf = build_admin_units()
    assert len(gdf) == 29
    assert list(gdf.columns) == ["adm_cd", "adm_nm", "geometry", "pop_total", "pop_foreign"]
    assert gdf["adm_cd"].is_unique


def test_admin_units_population_values_are_positive():
    from src.preprocess.admin_join import build_admin_units

    gdf = build_admin_units()
    assert (gdf["pop_total"] > 0).all()
    assert (gdf["pop_foreign"] >= 0).all()
    assert (gdf["pop_foreign"] <= gdf["pop_total"]).all()


def test_validate_point_in_polygon_mismatch_rate_is_low():
    from src.preprocess.admin_join import join_facilities_to_admin_dong, validate_point_in_polygon

    joined = join_facilities_to_admin_dong()
    mismatched = validate_point_in_polygon(joined)
    # 법정동 이름 매칭과 실제 좌표 기반 행정동이 다른 사례가 소수 존재할 수 있음
    # (reports/m1_legal_dong_mapping.md) — 전체 대비 비율이 낮은지만 확인한다.
    assert len(mismatched) / len(joined) < 0.05
