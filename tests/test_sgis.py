import os

import pytest
from dotenv import load_dotenv

load_dotenv()

KEYS_AVAILABLE = bool(os.getenv("SGIS_CONSUMER_KEY")) and bool(os.getenv("SGIS_CONSUMER_SECRET"))

pytestmark = pytest.mark.skipif(
    not KEYS_AVAILABLE,
    reason="SGIS_CONSUMER_KEY/SGIS_CONSUMER_SECRET이 .env에 없음 — gitignore 대상, 로컬에서만 발급 가능",
)


def test_get_access_token_returns_nonempty_string():
    from src.ingest.sgis import get_access_token

    token = get_access_token()
    assert isinstance(token, str)
    assert len(token) > 0


def test_fetch_pohang_boundaries_returns_29_dongs():
    from src.ingest.sgis import fetch_pohang_boundaries

    gdf = fetch_pohang_boundaries()
    assert len(gdf) == 29
    assert gdf["adm_cd_sgis"].is_unique
    assert str(gdf.crs) == "EPSG:5179"
    assert set(gdf["gu"]) == {"남구", "북구"}
