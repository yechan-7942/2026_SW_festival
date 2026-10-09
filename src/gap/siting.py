"""시설 입지 시뮬레이션 — 가상의 의료시설을 한 곳에 두면 격차가 얼마나 줄어드는가.

진단(어디가 부족한가)을 의사결정(어디에 두면 가장 효과적인가)으로 바꾸는 what-if 도구다.
기존 시설에 가상 시설 하나를 더한 뒤 2SFCA·격차 점수를 다시 계산하고 전후를 비교한다.
결과 해석과 한계는 reports/m7_siting.md 참고.

설계 선택
- 후보지는 행정동 수요점(집계구 인구 가중 중심점)이다. 실제 부지가 아니라 "이 동네 어딘가"다.
- 가상 시설의 capacity는 의사 수 기준이다(기본 3명 — 보건소 중앙값, 소형 의원~보건지소 규모).
- 정규화 기준은 **현재(기준선) 값의 min/max로 고정**한다. 시설을 더한 뒤 다시 min-max하면 최상위
  동 하나가 올라갈 때 나머지 전체의 점수가 같이 움직여 전후 비교가 왜곡된다. 접근성이 기준선
  최대를 넘으면 정규화 값을 1로 자른다.
- 평가 지표는 외국인 주민 가중 평균 격차 점수(Σ pop_foreign × gap / Σ pop_foreign)다. 외국인
  비율이 낮은 동에 시설을 둬도 이 값은 거의 줄지 않는다 — "외국인 주민이 체감하는 격차"를 본다.
"""

import numpy as np
import pandas as pd
import yaml

from src.access.catchment import DEFAULT_CONFIG_PATH, load_demand_points, load_supply_points
from src.access.two_sfca import compute_step1_ratios, compute_step2_access
from src.gap.score import ADMIN_UNITS_PATH, TOP_N, load_weights

SITING_OUTPUT_PATH = "data/processed/siting_candidates.parquet"
SITING_PLAN_OUTPUT_PATH = "data/processed/siting_plan.parquet"
DEFAULT_CAPACITY = 3.0  # 의사 수. 보건소 capacity 중앙값(3), 의원 평균(1.2)보다 큰 소형 거점
FAC_TYPE = "보건의료"


def _baseline(category_large: str = FAC_TYPE, config_path: str = DEFAULT_CONFIG_PATH):
    """수요점·공급점·임계거리(km)·수요 성분·가중치를 한 번에 읽는다."""
    demand = load_demand_points(config_path=config_path)
    supply = load_supply_points(category_large)
    with open(config_path, encoding="utf-8") as f:
        thresholds = yaml.safe_load(f)["distance_thresholds_km"]
    weights = load_weights()
    demand = demand.assign(demand_value=demand["pop_foreign"] / demand["pop_total"])
    return demand, supply, thresholds, weights


def _access(demand: pd.DataFrame, supply: pd.DataFrame, threshold_km: float) -> np.ndarray:
    threshold_m = threshold_km * 1000
    ratio = compute_step1_ratios(demand, supply, threshold_m)
    return compute_step2_access(demand, supply, ratio, threshold_m).to_numpy()


def _gap(access: np.ndarray, demand_value: np.ndarray, base_access: np.ndarray, weights: dict) -> np.ndarray:
    """기준선 min/max로 고정한 정규화로 격차 점수를 계산한다(모듈 docstring 참고)."""
    d_lo, d_hi = demand_value.min(), demand_value.max()
    a_lo, a_hi = base_access.min(), base_access.max()
    demand_norm = (demand_value - d_lo) / (d_hi - d_lo)
    access_norm = np.clip((access - a_lo) / (a_hi - a_lo), 0.0, 1.0)
    return weights["demand_weight"] * demand_norm + weights["access_weight"] * (1 - access_norm)


def _add_facility(supply: pd.DataFrame, x: float, y: float, capacity: float) -> pd.DataFrame:
    """가상 시설 한 곳을 공급점에 더한다(geometry 대신 x/y만 쓰는 2SFCA 입력 형태)."""
    from shapely.geometry import Point

    extra = supply.iloc[:1].copy()
    extra["fac_id"] = "SIM"
    extra["capacity"] = capacity
    extra["geometry"] = [Point(x, y)]
    return pd.concat([supply, extra], ignore_index=True)


def _weighted_mean(values: np.ndarray, weight: np.ndarray) -> float:
    return float((values * weight).sum() / weight.sum())


def simulate_sites(
    sites: list[str],
    capacity: float = DEFAULT_CAPACITY,
    threshold_km: float | None = None,
    category_large: str = FAC_TYPE,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> pd.DataFrame:
    """sites(adm_cd 목록) 각각의 수요점에 가상 시설을 **동시에** 두었을 때의 행정동별 전후 비교.

    반환: [adm_cd, adm_nm, gap_before, gap_after, gap_change, rank_before, rank_after,
          access_before, access_after]
    """
    demand, supply, thresholds, weights = _baseline(category_large, config_path)
    threshold_km = threshold_km if threshold_km is not None else load_default_threshold(config_path)
    unknown = set(sites) - set(demand["adm_cd"])
    if unknown:
        raise ValueError(f"알 수 없는 adm_cd: {sorted(unknown)}")

    dv = demand["demand_value"].to_numpy()
    before = _access(demand, supply, threshold_km)
    gap_before = _gap(before, dv, before, weights)

    new_supply = supply
    for adm_cd in sites:
        pt = demand.loc[demand["adm_cd"] == adm_cd, "geometry"].iloc[0]
        new_supply = _add_facility(new_supply, pt.x, pt.y, capacity)
    after = _access(demand, new_supply, threshold_km)
    gap_after = _gap(after, dv, before, weights)

    result = demand[["adm_cd", "adm_nm"]].copy()
    result["gap_before"] = gap_before
    result["gap_after"] = gap_after
    result["gap_change"] = gap_after - gap_before
    result["rank_before"] = pd.Series(gap_before).rank(ascending=False, method="min").astype(int).to_numpy()
    result["rank_after"] = pd.Series(gap_after).rank(ascending=False, method="min").astype(int).to_numpy()
    result["access_before"] = before
    result["access_after"] = after
    return result


def load_default_threshold(config_path: str = DEFAULT_CONFIG_PATH) -> float:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["access"]["default_threshold_km"]


def _summary(demand: pd.DataFrame, gap_before: np.ndarray, gap_after: np.ndarray) -> dict:
    """외국인 가중 평균 격차의 변화와 최우선 구간(TOP_N) 변화."""
    w = demand["pop_foreign"].to_numpy(dtype=float)
    top_before = set(np.argsort(-gap_before)[:TOP_N])
    top_after = set(np.argsort(-gap_after)[:TOP_N])
    return {
        "foreign_weighted_gap_before": _weighted_mean(gap_before, w),
        "foreign_weighted_gap_after": _weighted_mean(gap_after, w),
        "top_mean_before": float(np.sort(gap_before)[::-1][:TOP_N].mean()),
        "top_mean_after": float(np.sort(gap_after)[::-1][:TOP_N].mean()),
        "left_top": len(top_before - top_after),
    }


def rank_candidates(
    capacity: float = DEFAULT_CAPACITY,
    category_large: str = FAC_TYPE,
    config_path: str = DEFAULT_CONFIG_PATH,
    already_chosen: list[str] | None = None,
) -> pd.DataFrame:
    """29개 행정동 각각에 가상 시설 1곳을 둬 보고, 임계거리 1/3/5km별 효과를 비교한다.

    already_chosen에 이미 고른 후보지를 주면 그 시설들이 있는 상태에서 추가 1곳을 평가한다
    (greedy_plan이 쓴다). 반환 컬럼:
    [adm_cd, adm_nm, threshold_km, foreign_gap_before, foreign_gap_after, foreign_gap_reduction,
     own_gap_before, own_gap_after, own_gap_reduction, left_top, rank_in_threshold]
    효과는 외국인 가중 평균 격차의 감소량이다(클수록 좋다).
    """
    demand, supply, thresholds, weights = _baseline(category_large, config_path)
    dv = demand["demand_value"].to_numpy()
    chosen = already_chosen or []
    rows = []
    for threshold_km in thresholds:
        base_access = _access(demand, supply, threshold_km)
        current_supply = supply
        for adm_cd in chosen:
            pt = demand.loc[demand["adm_cd"] == adm_cd, "geometry"].iloc[0]
            current_supply = _add_facility(current_supply, pt.x, pt.y, capacity)
        current = _access(demand, current_supply, threshold_km)
        gap_cur = _gap(current, dv, base_access, weights)
        for i, row in demand.iterrows():
            if row["adm_cd"] in chosen:
                continue
            pt = row["geometry"]
            sim_supply = _add_facility(current_supply, pt.x, pt.y, capacity)
            after = _access(demand, sim_supply, threshold_km)
            gap_after = _gap(after, dv, base_access, weights)
            s = _summary(demand, gap_cur, gap_after)
            rows.append(
                {
                    "adm_cd": row["adm_cd"],
                    "adm_nm": row["adm_nm"],
                    "threshold_km": threshold_km,
                    "foreign_gap_before": s["foreign_weighted_gap_before"],
                    "foreign_gap_after": s["foreign_weighted_gap_after"],
                    "foreign_gap_reduction": s["foreign_weighted_gap_before"] - s["foreign_weighted_gap_after"],
                    "own_gap_before": float(gap_cur[i]),
                    "own_gap_after": float(gap_after[i]),
                    "own_gap_reduction": float(gap_cur[i] - gap_after[i]),
                    "left_top": s["left_top"],
                }
            )
    result = pd.DataFrame(rows)
    result["rank_in_threshold"] = (
        result.groupby("threshold_km")["foreign_gap_reduction"].rank(ascending=False, method="min").astype(int)
    )
    return result


def consensus_ranking(candidates: pd.DataFrame) -> pd.DataFrame:
    """임계거리 3개에서의 순위 평균·최악 순위로 후보지를 정렬한다.

    "어느 임계거리에서든 상위권인 입지"만 추천하기 위한 요약이다(프로젝트 전반의 원칙 —
    단일 임계값 결과로 단정하지 않는다). 반환: [adm_cd, adm_nm, rank_mean, rank_worst,
    mean_reduction, own_reduction_mean]
    """
    g = candidates.groupby(["adm_cd", "adm_nm"])
    out = g.agg(
        rank_mean=("rank_in_threshold", "mean"),
        rank_worst=("rank_in_threshold", "max"),
        mean_reduction=("foreign_gap_reduction", "mean"),
        own_reduction_mean=("own_gap_reduction", "mean"),
    ).reset_index()
    return out.sort_values(["rank_mean", "rank_worst"]).reset_index(drop=True)


def greedy_plan(
    k: int = 3,
    capacity: float = DEFAULT_CAPACITY,
    category_large: str = FAC_TYPE,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> pd.DataFrame:
    """k곳을 순서대로 하나씩 고른다 — 매번 임계거리 평균 효과가 가장 큰 동을 고르고 다음 단계는 그 시설이 있는 상태에서 평가한다.

    최적해가 아니라 탐욕 근사다(29곳 중 3곳이면 전수 탐색도 가능하지만 의사결정 설명이 "1순위부터 차례로"가
    더 직관적이라 이쪽을 쓴다). 반환: [step, adm_cd, adm_nm, mean_reduction, cumulative_reduction]
    """
    chosen: list[str] = []
    rows = []
    cumulative = 0.0
    for step in range(1, k + 1):
        cands = rank_candidates(capacity, category_large, config_path, already_chosen=chosen)
        best = consensus_ranking(cands).sort_values("mean_reduction", ascending=False).iloc[0]
        cumulative += best["mean_reduction"]
        chosen.append(best["adm_cd"])
        rows.append(
            {
                "step": step,
                "adm_cd": best["adm_cd"],
                "adm_nm": best["adm_nm"],
                "mean_reduction": float(best["mean_reduction"]),
                "cumulative_reduction": float(cumulative),
            }
        )
    return pd.DataFrame(rows)


def save_siting(
    candidates_path: str = SITING_OUTPUT_PATH,
    plan_path: str = SITING_PLAN_OUTPUT_PATH,
    capacity: float = DEFAULT_CAPACITY,
    k: int = 3,
) -> str:
    candidates = rank_candidates(capacity)
    candidates.to_parquet(candidates_path, index=False)
    greedy_plan(k, capacity).to_parquet(plan_path, index=False)
    return candidates_path


def dashboard_payload(
    capacities: tuple[float, ...] = (3.0, 6.0, 12.0),
    category_large: str = FAC_TYPE,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> dict:
    """대시보드가 브라우저에서 후보지·규모를 고를 때 쓸 미리 계산된 결과(기본 임계거리).

    JS에서 2SFCA를 다시 구현하지 않으려고 모든 후보지 × 규모 조합을 여기서 계산해 JSON으로 넘긴다
    (29 × 3 × 29개 숫자). 반환: names, gap_before, foreign, capacities, after{규모: {후보동: [동별 격차]}},
    consensus(임계거리 1/3/5km 종합 후보 순위, 기본 규모 기준).
    """
    demand, _, _, _ = _baseline(category_large, config_path)
    names = demand["adm_nm"].tolist()
    codes = demand["adm_cd"].tolist()
    gap_before = None
    after: dict[str, dict[str, list[float]]] = {}
    for capacity in capacities:
        per_site = {}
        for adm_cd, adm_nm in zip(codes, names):
            sim = simulate_sites([adm_cd], capacity=capacity, category_large=category_large, config_path=config_path)
            per_site[adm_nm] = [round(float(v), 4) for v in sim["gap_after"]]
            gap_before = [round(float(v), 4) for v in sim["gap_before"]]
        after[str(int(capacity))] = per_site
    consensus = consensus_ranking(rank_candidates(capacities[0], category_large, config_path))
    return {
        "names": names,
        "gap_before": gap_before,
        "foreign": [int(v) for v in demand["pop_foreign"]],
        "capacities": [int(c) for c in capacities],
        "after": after,
        "consensus": [
            {
                "adm_nm": r.adm_nm,
                "rank_mean": round(float(r.rank_mean), 1),
                "rank_worst": int(r.rank_worst),
                "reduction": round(float(r.mean_reduction), 5),
            }
            for r in consensus.itertuples()
        ],
    }


# admin_units 경로를 다른 모듈과 같은 상수로 노출 — 테스트가 임시 경로를 주입할 때 쓴다.
__all__ = [
    "ADMIN_UNITS_PATH",
    "DEFAULT_CAPACITY",
    "consensus_ranking",
    "dashboard_payload",
    "greedy_plan",
    "rank_candidates",
    "save_siting",
    "simulate_sites",
]
