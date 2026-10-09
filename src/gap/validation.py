"""격차 점수 외부 검증 — 같은 방법을 조금 바꿨을 때가 아니라, 다른 선택에서도 결론이 유지되는지 본다.

정답 데이터(실제로 취약하다고 확인된 동)가 없어서 정확도를 직접 잴 수 없다. 대신
여러 독립적인 선택(가중치, 접근성 지표)으로 같은 상위권이 나오는지 확인한다.
결과 해석은 reports/m6_validation.md 참고.
"""

import numpy as np
import pandas as pd
import yaml
from scipy.spatial import cKDTree

from src.access.catchment import load_demand_points, load_supply_points
from src.access.two_sfca import two_sfca, two_sfca_e2sfca
from src.gap.score import ADMIN_UNITS_PATH, TOP_N, min_max_normalize

WEIGHT_GRID = [round(i / 10, 1) for i in range(11)]


def weight_sweep(
    category_large: str = "보건의료",
    weights: list[float] = WEIGHT_GRID,
    config_path: str = "config/pipeline.yaml",
    admin_units_path: str = ADMIN_UNITS_PATH,
) -> pd.DataFrame:
    """수요 가중치(demand_weight) × 임계거리 전 조합의 행정동별 순위.

    config/weights.yaml의 0.5/0.5는 GM 검수로 정한 중립값이고 이론적 도출값이
    아니다. 그래서 가중치를 0~1 전체로 움직여 상위권이 어디까지 유지되는지 본다.
    접근성 가중치는 1 − demand_weight다. 반환: [adm_cd, threshold_km, demand_weight, gap_score, rank]
    """
    with open(config_path, encoding="utf-8") as f:
        thresholds = yaml.safe_load(f)["distance_thresholds_km"]
    units = pd.read_parquet(admin_units_path, columns=["adm_cd", "pop_total", "pop_foreign"]).set_index("adm_cd")

    frames = []
    for threshold_km in thresholds:
        access = two_sfca(category_large, threshold_km, config_path).set_index("adm_cd")["access_index"]
        demand_norm = min_max_normalize((units["pop_foreign"] / units["pop_total"]).loc[access.index])
        access_norm = min_max_normalize(access)
        for w in weights:
            score = w * demand_norm + (1 - w) * (1 - access_norm)
            frames.append(
                pd.DataFrame(
                    {
                        "adm_cd": score.index,
                        "threshold_km": threshold_km,
                        "demand_weight": w,
                        "gap_score": score.to_numpy(),
                        "rank": score.rank(ascending=False, method="min").astype(int).to_numpy(),
                    }
                )
            )
    return pd.concat(frames, ignore_index=True)


def alternative_access_indicators(
    category_large: str = "보건의료",
    threshold_km: float = 3,
    facilities_path: str = "data/processed/facilities.parquet",
) -> pd.DataFrame:
    """2SFCA와 다른 방식의 접근성 지표들(adm_cd 인덱스, 높을수록 접근성 좋음).

    - 2SFCA: 공식 지표(기준 임계거리)
    - E2SFCA: 거리감쇠를 넣은 변형(d_max = 임계거리 최댓값 5km)
    - 동 내 의사 수/인구: 거리 없이 행정동 경계 안 공급만 센다
    - 최근접 시설 거리: 공급량·경쟁을 무시하고 거리만 본다(부호를 뒤집어 높을수록 좋게)
    - 반경 내 의사 수: 경쟁(수요)을 무시한 누적 기회
    """
    demand = load_demand_points()
    supply = load_supply_points(category_large)
    facilities = pd.read_parquet(facilities_path, columns=["adm_cd", "category_large", "capacity"])
    facilities = facilities[facilities["category_large"] == category_large]
    idx = pd.Index(demand["adm_cd"], name="adm_cd")
    demand_xy = np.column_stack([demand.geometry.x, demand.geometry.y])
    tree = cKDTree(np.column_stack([supply.geometry.x, supply.geometry.y]))
    capacity = supply["capacity"].to_numpy()

    nearest_m, _ = tree.query(demand_xy)
    within = tree.query_ball_point(demand_xy, r=threshold_km * 1000)
    inside = facilities.groupby("adm_cd")["capacity"].sum().reindex(idx, fill_value=0)

    return pd.DataFrame(
        {
            "2SFCA": two_sfca(category_large, threshold_km).set_index("adm_cd")["access_index"].reindex(idx),
            "E2SFCA": two_sfca_e2sfca(category_large, 5).set_index("adm_cd")["access_index"].reindex(idx),
            "동 내 의사 수/인구": inside.to_numpy() / demand["pop_total"].to_numpy(),
            "최근접 시설 거리": -nearest_m,
            "반경 내 의사 수": [capacity[i].sum() for i in within],
        },
        index=idx,
    )


def compare_access_indicators(admin_units_path: str = ADMIN_UNITS_PATH, demand_weight: float = 0.5, **kwargs) -> pd.DataFrame:
    """지표별로 2SFCA와의 Spearman 순위상관과, 그 지표로 계산한 격차 점수 TOP_N을 비교한다.

    외국인 비율(수요)은 모든 지표에 공통이라, 격차 점수 TOP_N이 같다고 해서 접근성
    지표들이 서로 동의한다는 뜻은 아니다. 그래서 접근성만의 순위(access_rank_of)도 같이 본다.
    """
    indicators = alternative_access_indicators(**kwargs)
    units = pd.read_parquet(admin_units_path, columns=["adm_cd", "adm_nm", "pop_total", "pop_foreign"]).set_index("adm_cd")
    demand_norm = min_max_normalize((units["pop_foreign"] / units["pop_total"]).loc[indicators.index])
    base_rank = indicators["2SFCA"].rank()

    rows = []
    for name, values in indicators.items():
        gap = demand_weight * demand_norm + (1 - demand_weight) * (1 - min_max_normalize(values))
        top = gap.sort_values(ascending=False).head(TOP_N).index
        rows.append(
            {
                "indicator": name,
                "spearman_vs_2sfca": values.rank().corr(base_rank),
                "top_n": units.loc[top, "adm_nm"].tolist(),
            }
        )
    return pd.DataFrame(rows)


def access_rank_of(names: list[str], admin_units_path: str = ADMIN_UNITS_PATH, **kwargs) -> pd.DataFrame:
    """지표별 접근성 순위(1 = 29개 동 중 가장 나쁨) — 수요 성분을 뺀 접근성만의 위치."""
    indicators = alternative_access_indicators(**kwargs)
    nm = pd.read_parquet(admin_units_path, columns=["adm_cd", "adm_nm"]).set_index("adm_cd")["adm_nm"]
    ranks = indicators.rank(method="min").astype(int).rename(index=nm)
    return ranks.loc[names]


def road_gap_comparison(comparison: dict, admin_units_path: str = ADMIN_UNITS_PATH, demand_weight: float = 0.5) -> pd.DataFrame:
    """network.compare_road_vs_straight() 결과로 직선/도로 격차 점수 순위를 임계거리별로 비교한다.

    반환: 임계거리별 [threshold_km, spearman, straight_top_n, road_top_n, biggest_moves]
    """
    units = pd.read_parquet(admin_units_path, columns=["adm_cd", "adm_nm", "pop_total", "pop_foreign"]).set_index("adm_cd")
    rows = []
    for t, group in comparison["access"].groupby("threshold_km"):
        group = group.set_index("adm_cd")
        demand_norm = min_max_normalize((units["pop_foreign"] / units["pop_total"]).loc[group.index])
        ranks = {}
        for kind in ("straight", "road"):
            gap = demand_weight * demand_norm + (1 - demand_weight) * (1 - min_max_normalize(group[kind]))
            ranks[kind] = gap.rank(ascending=False, method="min").astype(int)
        moves = (ranks["road"] - ranks["straight"]).abs().sort_values(ascending=False).head(3)
        rows.append(
            {
                "threshold_km": t,
                "spearman": ranks["straight"].corr(ranks["road"], method="spearman"),
                "straight_top_n": units.loc[ranks["straight"].sort_values().index[:TOP_N], "adm_nm"].tolist(),
                "road_top_n": units.loc[ranks["road"].sort_values().index[:TOP_N], "adm_nm"].tolist(),
                "biggest_moves": [
                    f"{units.loc[a, 'adm_nm']} {ranks['straight'][a]}→{ranks['road'][a]}" for a in moves.index
                ],
            }
        )
    return pd.DataFrame(rows)


def top_n_share(sweep: pd.DataFrame, top_n: int = TOP_N) -> pd.Series:
    """각 행정동이 전체 조합 중 몇 %에서 TOP_N 안에 들었는지(adm_cd 인덱스, 내림차순)."""
    n_combos = sweep.groupby(["threshold_km", "demand_weight"]).ngroups
    hits = sweep[sweep["rank"] <= top_n].groupby("adm_cd").size()
    return (hits / n_combos).sort_values(ascending=False).rename("top_n_share")
