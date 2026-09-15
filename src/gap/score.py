from pathlib import Path

import pandas as pd
import yaml

from src.access.two_sfca import two_sfca

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
ACCESSIBILITY_PATH = "data/processed/accessibility.parquet"
GAP_SCORES_OUTPUT_PATH = "data/processed/gap_scores.parquet"
DEFAULT_WEIGHTS_PATH = "config/weights.yaml"


def load_weights(weights_path: str = DEFAULT_WEIGHTS_PATH) -> dict:
    """config/weights.yaml을 읽고 demand_weight + access_weight == 1.0을 검증한다.

    합이 1이 아니면 조용히 정규화하지 않고 바로 에러를 낸다 — GM이 편집한
    값에 오타가 있어도 결과가 그대로 나가는 것을 막기 위해서다.
    """
    with open(weights_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)["gap_score"]

    demand_weight = config["demand_weight"]
    access_weight = config["access_weight"]
    total = demand_weight + access_weight
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"demand_weight + access_weight = {total} (1.0이어야 함) — {weights_path} 확인 필요")

    return config


def min_max_normalize(series: pd.Series) -> pd.Series:
    """0~1 min-max 정규화. 전부 같은 값이면(범위=0) 순위를 매길 수 없으므로 0.5로 채운다.

    실제 29개 행정동 데이터에서는 발생하지 않지만, 도메인이 1곳뿐이거나
    합성 테스트 데이터를 쓸 때 ZeroDivisionError 대신 안전하게 처리한다.
    """
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.5, index=series.index)
    return (series - lo) / (hi - lo)


def _demand_metric(admin_units: pd.DataFrame, metric: str) -> pd.Series:
    if metric != "pop_foreign_ratio":
        raise ValueError(f"알 수 없는 demand_metric: {metric!r} (config/weights.yaml 확인)")
    return admin_units["pop_foreign"] / admin_units["pop_total"]


def _assign_priority_tier(gap_score: pd.Series) -> pd.Series:
    """4분위 기반 우선순위 구간(1=최우선 ~ 4=양호).

    실제 KMeans 등 클러스터링을 쓰지 않는다 — 표본이 29개뿐이고 아직 M4(NLP
    수요 신호)가 없어 "정책 유형"을 구분할 특징이 gap_score 하나뿐이다.
    특징이 하나뿐인 클러스터링은 사분위 구간 나누기와 결과가 같으므로,
    가짜 정교함 대신 사분위를 그대로 쓴다. M4가 붙으면 다변량 클러스터링으로
    교체해야 한다(README M3 "정책 유형 분류" 참고).
    """
    rank = gap_score.rank(ascending=False, method="min")
    tier = pd.cut(rank, bins=4, labels=[1, 2, 3, 4]).astype(int)
    return tier


def compute_gap_scores(
    accessibility: pd.DataFrame,
    admin_units_path: str = ADMIN_UNITS_PATH,
    weights_path: str = DEFAULT_WEIGHTS_PATH,
) -> pd.DataFrame:
    """격차 점수 = w_demand × 정규화(수요) + w_access × (1 − 정규화(접근성)).

    accessibility.parquet 계약([adm_cd, fac_type, access_index])을 받아
    gap_scores.parquet 계약([adm_cd, fac_type, gap_score, rank, cluster_id])을
    반환한다(README §5). 도메인(fac_type)별로 따로 정규화·순위를 매긴다 —
    도메인이 여러 개가 되면(향후 "금융" 재추가 시) 서로 다른 척도의 access_index를
    하나로 섞지 않기 위해서다.
    """
    weights = load_weights(weights_path)
    admin_units = pd.read_parquet(admin_units_path, columns=["adm_cd", "pop_total", "pop_foreign"])
    demand = admin_units.set_index("adm_cd")
    demand["demand_value"] = _demand_metric(demand, weights["demand_metric"])

    frames = []
    for fac_type, group in accessibility.groupby("fac_type"):
        merged = group.set_index("adm_cd").join(demand[["demand_value"]], how="left")
        if merged["demand_value"].isna().any():
            missing = merged.index[merged["demand_value"].isna()].tolist()
            raise ValueError(f"admin_units.parquet에 없는 adm_cd: {missing}")

        demand_norm = min_max_normalize(merged["demand_value"])
        access_norm = min_max_normalize(merged["access_index"])
        gap_score = weights["demand_weight"] * demand_norm + weights["access_weight"] * (1 - access_norm)

        result = pd.DataFrame(
            {
                "adm_cd": merged.index,
                "fac_type": fac_type,
                "gap_score": gap_score.to_numpy(),
            }
        )
        result["rank"] = result["gap_score"].rank(ascending=False, method="min").astype(int)
        result["cluster_id"] = _assign_priority_tier(result["gap_score"]).to_numpy()
        frames.append(result)

    return pd.concat(frames, ignore_index=True)[["adm_cd", "fac_type", "gap_score", "rank", "cluster_id"]]


def build_gap_scores(accessibility_path: str = ACCESSIBILITY_PATH, **kwargs) -> pd.DataFrame:
    """accessibility.parquet(기본 임계값=3km, config/pipeline.yaml 참고)를 읽어 격차 점수를 만든다."""
    accessibility = pd.read_parquet(accessibility_path)
    return compute_gap_scores(accessibility, **kwargs)


def save_gap_scores(path: str = GAP_SCORES_OUTPUT_PATH) -> str:
    df = build_gap_scores()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def gap_score_sensitivity(category_large: str, config_path: str = "config/pipeline.yaml", **kwargs) -> pd.DataFrame:
    """README §7 임계거리 민감도를 격차 점수까지 이어서 확인한다.

    reports/m2_two_sfca.md가 경고한 access_index 순위 불안정성이 gap_score에도
    그대로 이어지는지(=최우선 행정동 목록이 d0에 따라 바뀌는지) 확인하기 위한
    함수다. distance_thresholds_km 전부에 대해 두 임계값 사이의 gap_score/rank를
    나란히 반환한다.
    """
    with open(config_path, encoding="utf-8") as f:
        thresholds = yaml.safe_load(f)["distance_thresholds_km"]

    wide = None
    for threshold_km in thresholds:
        accessibility = two_sfca(category_large, threshold_km, config_path)[["adm_cd", "fac_type", "access_index"]]
        scored = compute_gap_scores(accessibility, **kwargs).set_index("adm_cd")
        col = f"{threshold_km}km_gap_score"
        rank_col = f"{threshold_km}km_rank"
        column = pd.DataFrame({col: scored["gap_score"], rank_col: scored["rank"]})
        wide = column if wide is None else wide.join(column)

    return wide.reset_index()


if __name__ == "__main__":
    path = save_gap_scores()
    print(f"저장됨: {path}")
    df = pd.read_parquet(path)
    print(df.sort_values("rank").head(10).to_string(index=False))
