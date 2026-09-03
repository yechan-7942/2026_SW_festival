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


def distance_weight(dist_m: np.ndarray, d_max_m: float) -> np.ndarray:
    """E2SFCA 선형 거리감쇠 가중치 — d=0이면 1.0, d=d_max면 0.0으로 선형 감소.

    Luo & Qi(2009)의 구간별 고정 가중치표(1.0/0.42/0.09 등, 미국 자동차 통근
    기준으로 보정된 값) 대신 선형 감쇠를 쓴다 — 새 매직넘버(구간 경계·가중치
    값) 없이 이미 config/pipeline.yaml에 있는 distance_thresholds_km의
    최댓값 하나만으로 정의된다. reports/m2_e2sfca.md 참고.
    """
    return np.clip(1.0 - dist_m / d_max_m, 0.0, 1.0)


def compute_step1_ratios_e2sfca(demand: pd.DataFrame, supply: pd.DataFrame, d_max_m: float) -> pd.Series:
    """E2SFCA 1단계 — compute_step1_ratios()의 이진 catchment를 거리감쇠 가중합으로 대체.

    R_j = capacity_j / Σ(weight(d_ij) × pop_total_i), d_ij ≤ d_max_m인 i만.
    """
    demand_coords = _coords(demand)
    supply_coords = _coords(supply)
    demand_tree = cKDTree(demand_coords)
    neighbor_idx = demand_tree.query_ball_point(supply_coords, r=d_max_m)
    pop_total = demand["pop_total"].to_numpy()

    catchment_pop = np.zeros(len(supply))
    for j, idx in enumerate(neighbor_idx):
        if not idx:
            continue
        dist = np.linalg.norm(demand_coords[idx] - supply_coords[j], axis=1)
        catchment_pop[j] = (pop_total[idx] * distance_weight(dist, d_max_m)).sum()

    ratio = np.divide(
        supply["capacity"].to_numpy(dtype=float),
        catchment_pop,
        out=np.zeros(len(supply)),
        where=catchment_pop > 0,
    )
    return pd.Series(ratio, name="ratio")


def compute_step2_access_e2sfca(demand: pd.DataFrame, supply: pd.DataFrame, ratio: pd.Series, d_max_m: float) -> pd.Series:
    """E2SFCA 2단계 — compute_step2_access()의 이진 catchment를 거리감쇠 가중합으로 대체.

    access_i = Σ(weight(d_ij) × R_j), d_ij ≤ d_max_m인 j만.
    """
    demand_coords = _coords(demand)
    supply_coords = _coords(supply)
    supply_tree = cKDTree(supply_coords)
    neighbor_idx = supply_tree.query_ball_point(demand_coords, r=d_max_m)
    ratio_values = ratio.to_numpy()

    access = np.zeros(len(demand))
    for i, idx in enumerate(neighbor_idx):
        if not idx:
            continue
        dist = np.linalg.norm(supply_coords[idx] - demand_coords[i], axis=1)
        access[i] = (ratio_values[idx] * distance_weight(dist, d_max_m)).sum()

    return pd.Series(access, name="access_index")


def two_sfca_e2sfca(category_large: str, d_max_km: float, config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """E2SFCA(선형 거리감쇠) 버전 — two_sfca()와 동일한 출력 스키마(README §5).

    바닐라 two_sfca()는 그대로 두고(M2 리포트에 이미 결과가 박제됨) 비교용
    변형을 병렬로 추가한 것. accessibility.parquet(공식 산출물)에는 반영하지
    않는다 — reports/m2_e2sfca.md의 검증 결과가 나오기 전까지는 build_accessibility()가
    바닐라 two_sfca()만 사용한다.
    """
    demand = load_demand_points()
    supply = load_supply_points(category_large)
    if len(supply) == 0:
        raise ValueError(f"'{category_large}' 대분류에 해당하는 시설이 facilities.parquet에 없습니다.")

    d_max_m = d_max_km * 1000
    ratio = compute_step1_ratios_e2sfca(demand, supply, d_max_m)
    access = compute_step2_access_e2sfca(demand, supply, ratio, d_max_m)

    result = demand[["adm_cd", "adm_nm"]].copy()
    result["fac_type"] = category_large
    result["access_index"] = access.to_numpy()
    return result


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


def e2sfca_distance_sensitivity(category_large: str, config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """distance_sensitivity()의 E2SFCA 버전 — 같은 distance_thresholds_km를 d_max로 써서 비교."""
    config = load_config(config_path)
    thresholds = config["distance_thresholds_km"]

    wide = None
    for threshold_km in thresholds:
        result = two_sfca_e2sfca(category_large, threshold_km, config_path)
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

    names = two_sfca_e2sfca(category_large, thresholds[0], config_path).set_index("adm_cd")["adm_nm"]
    wide.insert(0, "adm_nm", names)
    return wide.reset_index()


def compare_rank_volatility(category_large: str, config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """바닐라 2SFCA vs E2SFCA의 임계거리별 순위 변동폭 비교 — E2SFCA 도입 근거 검증용.

    각 행정동마다 distance_thresholds_km 전부에서 나온 순위의 (최댓값-최솟값)을
    구해, 0보다 큰(=한 번이라도 순위가 바뀐) 행정동 수와 평균 변동폭을 두 방식
    간에 비교한다. reports/m2_e2sfca.md 참고.
    """
    vanilla = distance_sensitivity(category_large, config_path)
    enhanced = e2sfca_distance_sensitivity(category_large, config_path)

    config = load_config(config_path)
    rank_cols = [f"{t}km_rank" for t in config["distance_thresholds_km"]]

    def volatility(df: pd.DataFrame) -> tuple:
        spread = df[rank_cols].max(axis=1) - df[rank_cols].min(axis=1)
        return int((spread > 0).sum()), float(spread.mean())

    vanilla_unstable, vanilla_mean_spread = volatility(vanilla)
    enhanced_unstable, enhanced_mean_spread = volatility(enhanced)

    return {
        "total_dong": len(vanilla),
        "vanilla_unstable_count": vanilla_unstable,
        "vanilla_mean_rank_spread": vanilla_mean_spread,
        "e2sfca_unstable_count": enhanced_unstable,
        "e2sfca_mean_rank_spread": enhanced_mean_spread,
    }


if __name__ == "__main__":
    path = save_accessibility()
    print(f"저장됨: {path}")
    df = pd.read_parquet(path)
    print(df.groupby("fac_type")["access_index"].describe())
