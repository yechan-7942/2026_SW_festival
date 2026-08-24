import pandas as pd

from src.preprocess.crs import TARGET_CRS, reproject_points


def test_reproject_points_sets_target_crs():
    df = pd.DataFrame({"lon": [129.365], "lat": [36.019]})
    result = reproject_points(df)
    assert str(result.crs) == TARGET_CRS


def test_reproject_points_output_is_within_south_korea_epsg5179_range():
    # EPSG:5179는 한반도 전역을 커버하도록 원점을 잡은 좌표계라, 남한 내 지점은
    # 대략 x 700000~1300000, y 1400000~2300000 범위에 들어온다.
    df = pd.DataFrame({"lon": [129.365], "lat": [36.019]})
    result = reproject_points(df)
    point = result.geometry.iloc[0]
    assert 700_000 < point.x < 1_300_000
    assert 1_400_000 < point.y < 2_300_000


def test_reproject_points_is_deterministic():
    df = pd.DataFrame({"lon": [129.365, 129.4], "lat": [36.019, 36.05]})
    first = reproject_points(df)
    second = reproject_points(df)
    assert list(first.geometry) == list(second.geometry)
