from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from src.access.catchment import DEFAULT_CONFIG_PATH, load_config, load_demand_points, load_supply_points

ACCESSIBILITY_OUTPUT_PATH = "data/processed/accessibility.parquet"


def _coords(gdf) -> np.ndarray:
    return np.column_stack([gdf.geometry.x, gdf.geometry.y])


def compute_step1_ratios(demand: pd.DataFrame, supply: pd.DataFrame, threshold_m: float) -> pd.Series:
    """1단계: 각 시설의 공급/수요 비율 R_j = capacity_j / (임계거리 내 pop_total 합).

    자기 catchment 안에 행정동 중심점이 하나도 없는 시설(threshold_m 밖의 고립
    시설)은 catchment_pop=0이라 R_j를 0으로 둔다 — 어차피 2단계에서도 어떤
    수요점의 catchment에도 들지 못하므로(거리는 대칭) 결과에 영향이 없다.
    """
    demand_tree = cKDTree(_coords(demand))
    neighbor_idx = demand_tree.query_ball_point(_coords(supply), r=threshold_m)
    pop_total = demand["pop_total"].to_numpy()
    catchment_pop = np.array([pop_total[idx].sum() for idx in neighbor_idx], dtype=float)
    ratio = np.divide(
        supply["capacity"].to_numpy(dtype=float),
        catchment_pop,
        out=np.zeros(len(supply)),
        where=catchment_pop > 0,
    )
    return pd.Series(ratio, name="ratio")


def compute_step2_access(demand: pd.DataFrame, supply: pd.DataFrame, ratio: pd.Series, threshold_m: float) -> pd.Series:
    """2단계: 각 행정동에서 임계거리 내 도달 가능한 시설들의 R_j 합 = 접근성 지수."""
    supply_tree = cKDTree(_coords(supply))
    neighbor_idx = supply_tree.query_ball_point(_coords(demand), r=threshold_m)
    ratio_values = ratio.to_numpy()
    access = np.array([ratio_values[idx].sum() for idx in neighbor_idx])
    return pd.Series(access, name="access_index")


def two_sfca(category_large: str, threshold_km: float, config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """한 공급 도메인(category_large)·한 임계거리(threshold_km)에 대한 2SFCA 접근성 지수.

    반환 컬럼은 README §5 accessibility.parquet 계약([adm_cd, fac_type, access_index])을
    따른다. fac_type에는 세부 업종(예: 종합병원·편의점)이 아니라 category_large를
    그대로 채운다 — M2가 목표로 하는 단위는 "의료·금융 2종" 도메인 지수이고,
    세부 업종별로 쪼개면 각 catchment의 표본이 너무 작아져 비율이 불안정해진다.
    """
    demand = load_demand_points()
    supply = load_supply_points(category_large)
    if len(supply) == 0:
        raise ValueError(f"'{category_large}' 대분류에 해당하는 시설이 facilities.parquet에 없습니다.")

    threshold_m = threshold_km * 1000
    ratio = compute_step1_ratios(demand, supply, threshold_m)
    access = compute_step2_access(demand, supply, ratio, threshold_m)

    result = demand[["adm_cd", "adm_nm"]].copy()
    result["fac_type"] = category_large
    result["access_index"] = access.to_numpy()
    return result


def build_accessibility(config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """config의 access.domains 전부에 대해 access.default_threshold_km로 2SFCA를 돌린다.

    공급이 0건인 도메인(현재 "금융" — reports/m2_commercial.md)은 에러를 내지
    않고 건너뛴다. README §7의 설계 원칙("데이터 확보 실패가 시스템 전체를
    멈추지 않게 한다")을 그대로 따른 것이다 — 건너뛴 도메인은 표준출력에 남는다.
    """
    config = load_config(config_path)
    access_cfg = config["access"]
    threshold_km = access_cfg["default_threshold_km"]

    frames = []
    for domain in access_cfg["domains"]:
        category_large = domain["category_large"]
        try:
            frames.append(two_sfca(category_large, threshold_km, config_path))
        except ValueError as e:
            print(f"[build_accessibility] 건너뜀: {e}")

    if not frames:
        raise RuntimeError("모든 access.domains가 공급 0건이라 accessibility.parquet를 만들 수 없습니다.")

    combined = pd.concat(frames, ignore_index=True)
    return combined[["adm_cd", "fac_type", "access_index"]]


def save_accessibility(path: str = ACCESSIBILITY_OUTPUT_PATH) -> str:
    df = build_accessibility()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def distance_sensitivity(category_large: str, config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """README §7 임계거리 민감도 분석 — config의 distance_thresholds_km 전부로 2SFCA를
    돌려 행정동별 access_index·순위를 나란히 비교한다.
    """
    config = load_config(config_path)
    thresholds = config["distance_thresholds_km"]

    wide = None
    for threshold_km in thresholds:
        result = two_sfca(category_large, threshold_km, config_path)
        result = result.set_index("adm_cd")
        col = f"{threshold_km}km"
        rank_col = f"{threshold_km}km_rank"
        column = pd.DataFrame(
            {
                col: result["access_index"],
                rank_col: result["access_index"].rank(ascending=False, method="min").astype(int),
            }
        )
        wide = column if wide is None else wide.join(column)

    names = two_sfca(category_large, thresholds[0], config_path).set_index("adm_cd")["adm_nm"]
    wide.insert(0, "adm_nm", names)
    return wide.reset_index()


if __name__ == "__main__":
    path = save_accessibility()
    print(f"저장됨: {path}")
    df = pd.read_parquet(path)
    print(df.groupby("fac_type")["access_index"].describe())
