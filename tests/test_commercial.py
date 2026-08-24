import os

import pytest
from dotenv import load_dotenv

load_dotenv()

KEY_AVAILABLE = bool(os.getenv("DATA_GO_KR_API_KEY"))
SGIS_AVAILABLE = bool(os.getenv("SGIS_CONSUMER_KEY")) and bool(os.getenv("SGIS_CONSUMER_SECRET"))

pytestmark = pytest.mark.skipif(
    not (KEY_AVAILABLE and SGIS_AVAILABLE),
    reason="DATA_GO_KR_API_KEY 또는 SGIS 키가 .env에 없음 — gitignore 대상, 로컬에서만 발급 가능",
)


def test_dong_query_points_caps_large_dongs_at_max_radius():
    from src.ingest.commercial import _dong_query_points, load_config

    config = load_config()
    max_radius = config["commercial"]["max_radius_m"]
    points = _dong_query_points()
    assert len(points) == 29
    assert (points["radius_m"] <= max_radius).all()
    # 흥해읍(실측 11.0km)·죽장면(실측 18.6km)은 상한을 넘는 걸로 확인됨 — 캡 적용 여부 확인
    capped = points[points["adm_nm"].isin(["흥해읍", "죽장면"])]
    assert (capped["radius_m"] == max_radius).all()


def test_fetch_stores_in_radius_excludes_medical_and_sets_capacity():
    from src.ingest.commercial import fetch_stores_in_radius

    df = fetch_stores_in_radius(cx=129.365, cy=36.019, radius_m=1000, num_of_rows=1000)
    assert len(df) > 0
    assert (df["capacity"] == 1).all()
    assert (df["category_large"] != "보건의료").all()
    assert df["fac_id"].is_unique
