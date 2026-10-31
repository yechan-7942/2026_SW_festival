"""전체 주민 기반 접근성과 외국인 집중도/인구수의 정책 시나리오 비교.

기존 점수·카드·입지 결과는 변경하지 않는다. 원본 총의사수 스냅샷을 별도로
보관해 약국 개소 수와 진료 인력 단위를 섞지 않은 비교를 오프라인으로 재현한다.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np

from src.access.catchment import load_config, load_demand_points, load_supply_points
from src.access.two_sfca import compute_step1_ratios, compute_step2_access
from src.gap.score import min_max_normalize
from src.ingest.datagokr import HOSPITAL_PATH

SUPPLY_SNAPSHOT = "data/processed/medical_practitioners.parquet"
SNAPSHOT_METADATA = "data/processed/medical_practitioners.meta.json"
COMPARISON_PATH = "outputs/comparison.json"
SUPPLIES = {
    "mixed": {"label": "기존 혼합 공급 (비교 기준선)", "unit": "의사 수·시설 개소의 혼합 단위"},
    "practitioners": {"label": "약국 제외 · 원본 총의사수", "unit": "원본 총의사수 (치과·한방 등 포함)"},
    "pharmacies": {"label": "약국만 · 개소 수", "unit": "약국 개소 수"},
}
METRICS = {
    "ratio": {"label": "외국인 비율 중심", "meaning": "지역 인구 중 외국인 주민의 상대적 집중도를 반영합니다."},
    "count": {"label": "외국인 인구수 중심", "meaning": "외국인 주민이 많이 거주하는 지역을 우선 검토하는 관점입니다."},
}


def prepare_practitioner_snapshot(hospital_path: str = HOSPITAL_PATH) -> str:
    """원본의 0을 1로 바꾸지 않은 비약국 공급 스냅샷. API 호출 없음."""
    supply = load_supply_points("보건의료")
    ids = supply.loc[supply.fac_type != "약국", ["fac_id", "fac_type"]]
    raw = pd.read_excel(hospital_path, usecols=["암호화요양기호", "총의사수"]).rename(
        columns={"암호화요양기호": "fac_id", "총의사수": "recorded_practitioners"}
    )
    if raw.fac_id.duplicated().any():
        raise ValueError("원본 병의원 시설 식별자가 중복됩니다.")
    snapshot = ids.merge(raw, on="fac_id", how="left", validate="one_to_one")
    if snapshot.recorded_practitioners.isna().any() or (snapshot.recorded_practitioners < 0).any():
        raise ValueError("대조되지 않거나 음수인 원본 총의사수가 있습니다.")
    path = Path(SUPPLY_SNAPSHOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_parquet(path, index=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": hospital_path,
        "source_sha256": hashlib.sha256(Path(hospital_path).read_bytes()).hexdigest(),
        "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": len(snapshot),
        "recorded_practitioners": float(snapshot.recorded_practitioners.sum()),
        "zero_count": int(snapshot.recorded_practitioners.eq(0).sum()),
        "meaning": "심평원 원본 총의사수. 치과·한방 등 포함, 약국 제외, 0 대체 없음. 실제 진료 역량이나 이용량이 아님.",
    }
    Path(SNAPSHOT_METADATA).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return str(path)


def scenario_scores(demand: pd.DataFrame, supply: pd.DataFrame, threshold_km: float,
                    metric: str, demand_weight: float = 0.5) -> pd.DataFrame:
    """하나의 공급·거리·수요 조합. 전체 주민이 공유 공급의 경쟁 분모다."""
    if metric not in METRICS:
        raise ValueError(f"알 수 없는 수요 지표: {metric}")
    if not 0 <= demand_weight <= 1 or threshold_km <= 0:
        raise ValueError("가중치는 0~1, 거리는 양수여야 합니다.")
    if supply.empty or not np.isfinite(supply.capacity).all() or (supply.capacity < 0).any():
        raise ValueError("공급은 비어 있지 않고 공급량은 음수가 아니어야 합니다.")
    if (not np.isfinite(demand[["pop_total", "pop_foreign"]].to_numpy()).all()
        or (demand.pop_total <= 0).any() or (demand.pop_foreign < 0).any()
        or demand.adm_cd.duplicated().any()):
        raise ValueError("수요점은 고유해야 하며 전체 인구가 양수여야 합니다.")
    ratios = compute_step1_ratios(demand, supply, threshold_km * 1000)
    access = compute_step2_access(demand, supply, ratios, threshold_km * 1000)
    out = demand[["adm_cd", "adm_nm", "pop_foreign", "pop_total"]].reset_index(drop=True).copy()
    out["foreign_ratio"] = out.pop_foreign / out.pop_total
    out["access_index"] = access.to_numpy()
    out["demand_norm"] = min_max_normalize(out.foreign_ratio if metric == "ratio" else out.pop_foreign)
    out["access_norm"] = min_max_normalize(out.access_index)
    out["gap_score"] = demand_weight * out.demand_norm + (1 - demand_weight) * (1 - out.access_norm)
    out["rank"] = out.gap_score.rank(ascending=False, method="min").astype(int)
    return out.sort_values(["rank", "adm_cd"]).reset_index(drop=True)


def build_comparison_payload(config_path: str = "config/pipeline.yaml") -> dict:
    config = load_config(config_path)
    demand = load_demand_points(config_path=config_path)
    mixed = load_supply_points("보건의료")
    snapshot = pd.read_parquet(SUPPLY_SNAPSHOT)
    if snapshot.fac_id.duplicated().any():
        raise ValueError("원본 총의사수 스냅샷의 시설 식별자가 중복됩니다.")
    clinical = mixed[mixed.fac_type != "약국"].merge(
        snapshot[["fac_id", "recorded_practitioners"]], on="fac_id", how="left", validate="one_to_one"
    )
    if (not np.isfinite(clinical.recorded_practitioners).all()
        or (clinical.recorded_practitioners < 0).any()):
        raise ValueError("시설 목록과 원본 총의사수 스냅샷이 일치하지 않습니다. 스냅샷을 다시 준비하세요.")
    clinical["capacity"] = clinical.recorded_practitioners
    clinical = clinical[clinical.capacity > 0].reset_index(drop=True)
    pharmacies = mixed[mixed.fac_type == "약국"].copy().reset_index(drop=True)
    pharmacies["capacity"] = 1.0
    pools = {"mixed": mixed, "practitioners": clinical, "pharmacies": pharmacies}
    demand_weight = config.get("comparison", {}).get("demand_weight", 0.5)
    if config["access"]["default_threshold_km"] not in config["distance_thresholds_km"]:
        raise ValueError("기본 임계거리가 비교 임계거리 목록에 없습니다.")
    cases = []
    for key, supply in pools.items():
        for km in config["distance_thresholds_km"]:
            for metric in METRICS:
                frame = scenario_scores(demand, supply, km, metric, demand_weight)
                cases.append({"supply": key, "threshold_km": km, "metric": metric,
                              "rows": frame.to_dict("records")})
    return {
        "version": "policy-scenarios-v1",
        "default_threshold_km": config["access"]["default_threshold_km"],
        "thresholds_km": config["distance_thresholds_km"],
        "demand_weight": demand_weight, "access_weight": 1 - demand_weight,
        "metrics": METRICS,
        "supplies": {key: {**SUPPLIES[key], "facilities": len(pool), "capacity": float(pool.capacity.sum())}
                     for key, pool in pools.items()},
        "cases": cases,
        "scope": "직선거리·전체 주민 분모·동일한 인구가중 중심점. 각 조건에서 별도로 min-max 정규화. 실제 의료 수요·정책 효과는 측정하지 않음.",
    }


def save_comparison(path: str = COMPARISON_PATH) -> str:
    payload = build_comparison_payload()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frames = []
    for case in payload["cases"]:
        frames.append(pd.DataFrame(case["rows"]).assign(supply=case["supply"], threshold_km=case["threshold_km"], metric=case["metric"]))
    pd.concat(frames, ignore_index=True).to_csv(target.with_name("comparison_ranks.csv"), index=False, encoding="utf-8-sig")
    return str(target)


def comparison_markdown(payload: dict, heading: str = "##") -> str:
    """화면·보고서·검토 자료가 같은 계산 결과를 사용하도록 만드는 공통 표."""
    km = payload["default_threshold_km"]
    labels = {"mixed": "기존 혼합 공급", "practitioners": "약국 제외·원본 총의사수", "pharmacies": "약국 개소 수"}
    lines = [f"{heading} 정책 목적·공급 기준 비교", "",
             f"직선 {km}km, 전체 주민 분모, 동일한 인구가중 중심점, 수요 {payload['demand_weight']:g}·접근성 결핍 {payload['access_weight']:g}를 사용했다. "
             "각 조건에서 따로 min-max 정규화하므로 서로 다른 조건의 점수 차이는 실제 개선량이 아니다.", "",
             "| 공급 기준 | 외국인 지표 | 상위 5곳 (순위순) |", "|---|---|---|"]
    for case in payload["cases"]:
        if case["threshold_km"] == km:
            names = " → ".join(row["adm_nm"] for row in case["rows"][:5])
            lines.append(f"| {labels[case['supply']]} | {'비율' if case['metric'] == 'ratio' else '절대 인구'} | {names} |")
    clinical = payload["supplies"]["practitioners"]
    pharmacies = payload["supplies"]["pharmacies"]
    lines += ["", f"분리 비교의 진료 인력 공급량은 원본 총의사수 {clinical['capacity']:,.0f}명 "
              f"(양수인 시설 {clinical['facilities']}곳), 약국은 {pharmacies['facilities']}개소다. "
              "총의사수에는 치과·한방 등이 포함되며 진료 기능·운영시간·실제 이용 가능성이 동일하다는 뜻은 아니다.", "",
              "비율 중심은 지역 내 상대적 집중도, 인구수 중심은 외국인 주민이 많이 거주하는 지역을 반영한다. "
              "어느 관점이 적합한지는 정책 목적에 대한 실제 담당자 판단이 필요하며, 두 관점 모두 실제 의료 수요량을 측정한 값은 아니다.", "",
              "기존 지도·정책 카드·입지 시뮬레이션은 혼합 공급·비율 기준선을 유지한다. 비교 결과가 기존 정책의 효과나 현장 취약성을 검증하는 것은 아니다."]
    return "\n".join(lines)
