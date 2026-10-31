"""기본 분석 1위 지역의 시설 추가 시나리오와 재현 가능한 정책 연결.

기존 혼합 공급 정의를 유지한다. 분리 접근성은 해당 유형만으로 계산하며
서로 다른 공급 단위의 변화율을 정책 효과 비교에 사용하지 않는다. LLM 호출 없음.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.access.catchment import load_config
from src.gap.siting import _access, _add_facility, _baseline, _gap


def build_siting_evidence(config_path="config/pipeline.yaml") -> dict:
    demand, supply, thresholds, weights = _baseline(config_path=config_path)
    config = load_config(config_path)
    km_default = config["access"]["default_threshold_km"]
    if km_default not in thresholds:
        raise ValueError("기본 임계거리가 시나리오 거리 목록에 없습니다.")
    base = _access(demand, supply, km_default)
    dv = demand.demand_value.to_numpy()
    gaps = _gap(base, dv, base, weights)
    # 동점이면 코드순. 입지 효과 순위가 아니라 기본 분석으로 선정한다.
    ranked = demand.assign(gap=gaps).sort_values(["gap", "adm_cd"], ascending=[False, True])
    selected = ranked.iloc[0]
    reference = pd.read_parquet("data/processed/gap_scores.parquet")
    reference = reference[reference.fac_type == "보건의료"].set_index("adm_cd")
    if not np.allclose(gaps, reference.loc[demand.adm_cd].gap_score, atol=1e-12, rtol=0):
        raise ValueError("기본 분석과 시나리오 기준선이 다릅니다. access/gap 단계를 먼저 확인하세요.")
    cases = []
    for kind, label, capacity in [("hospital", "병의원 1곳 · 의사 3명 가정", 3.),
                                  ("pharmacy", "약국 1개소 가정", 1.)]:
        pool = supply[supply.fac_type.eq("약국") if kind == "pharmacy" else supply.fac_type.ne("약국")]
        for km in thresholds:
            before = _access(demand, supply, km)
            after = _access(demand, _add_facility(supply, selected.geometry.x, selected.geometry.y, capacity), km)
            component_before = _access(demand, pool, km)
            component_after = _access(demand, _add_facility(pool, selected.geometry.x, selected.geometry.y, capacity), km)
            gb = _gap(before, dv, before, weights)
            ga = _gap(after, dv, before, weights)
            rows = demand[["adm_cd", "adm_nm", "pop_total", "pop_foreign"]].copy()
            rows["distance_km"] = demand.geometry.distance(selected.geometry) / 1000
            rows["access_before"], rows["access_after"] = before, after
            rows["access_change"] = after - before
            rows["change_pct"] = np.divide((after - before) * 100, before,
                                                    out=np.full(len(before), np.nan), where=before > 0)
            rows["component_before"], rows["component_after"] = component_before, component_after
            rows["gap_before"], rows["gap_after"] = gb, ga
            rows["rank_before"] = pd.Series(gb).rank(ascending=False, method="min").astype(int).to_numpy()
            rows["rank_after"] = pd.Series(ga).rank(ascending=False, method="min").astype(int).to_numpy()
            rows["affected"] = after - before > 1e-12
            rows = rows.sort_values(["distance_km", "adm_cd"])
            cases.append({"kind": kind, "label": label, "capacity_added": capacity, "threshold_km": km,
                          "component_unit": "약국 개소/주민" if kind == "pharmacy" else "병의원 공급 프록시/주민 (총의사수 0→1)",
                          "rows": json.loads(rows.to_json(orient="records", force_ascii=False, double_precision=15))})
    source_paths = ["config/pipeline.yaml", "config/weights.yaml", "data/processed/facilities.parquet",
                    "data/processed/admin_units.parquet", "data/processed/pop_centroids.parquet",
                    "data/processed/gap_scores.parquet", "data/processed/policy_cards.parquet",
                    "src/policy/siting_evidence.py"]
    return {"version": "siting-evidence-v1", "selected": {"adm_cd": selected.adm_cd, "adm_nm": selected.adm_nm,
            "rank": int(reference.loc[selected.adm_cd, "rank"]), "gap_score": float(gaps[demand.adm_cd.eq(selected.adm_cd)][0]),
            "pop_foreign": int(selected.pop_foreign), "pop_total": int(selected.pop_total)},
            "selection_reason": "기본 혼합 공급·외국인 비율 분석의 격차 점수 1위. 최적 입지나 필요 시설 수를 뜻하지 않음.",
            "default_threshold_km": km_default, "weights": weights,
            "supply_definition": "병의원 총의사수(0은 1로 대체) + 약국 1개소당 1의 기존 혼합 공급. 분리 접근성도 같은 공급량 정의를 유지하며 원본 총의사수 비교 화면과 구별함.",
            "supply_totals": {"hospital": float(supply.loc[supply.fac_type.ne('약국'), 'capacity'].sum()),
                              "pharmacy": float(supply.loc[supply.fac_type.eq('약국'), 'capacity'].sum())},
            "scope": "동별 인구가중 중심점에 가상 시설 1곳 추가, 전체 주민 분모, 직선거리, 전후 기준선 min/max 고정. 실제 부지·도로 이동·진료과·운영시간·인력·예산·이용량 미반영. 시설 필요 수·정책 효과 산정 아님.",
            "source_sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_paths}, "cases": cases}


def selected_case(payload, kind="hospital", km=None):
    return next(c for c in payload["cases"] if c["kind"] == kind and
                c["threshold_km"] == (payload["default_threshold_km"] if km is None else km))


def link_policy_card(original: str, payload: dict) -> str:
    """원문의 사실·해석·출처를 보존하고 선정 지역의 대응을 계산 결과에 연결한다."""
    case = selected_case(payload)
    row = next(r for r in case["rows"] if r["adm_cd"] == payload["selected"]["adm_cd"])
    pharmacy = next(r for r in selected_case(payload, "pharmacy")["rows"] if r["adm_cd"] == row["adm_cd"])
    pct = "기준값 0으로 변화율 미산출" if row["change_pct"] is None else f"{row['change_pct']:.2f}%"
    replacement = (
        "[우선 대응] 공간적 공급 보완 시나리오를 먼저 현장 검토\n"
        f"- 제안: {row['adm_nm']}의 인구가중 중심점에 병의원 1곳(의사 3명 가정)을 추가한 경우와 약국 1개소 추가를 각각 비교합니다.\n"
        f"- 계산 근거: 직선 {case['threshold_km']}km 혼합 공급 접근성은 병의원 시나리오에서 {row['access_before']:.8f} → {row['access_after']:.8f}({pct}), "
        f"약국 시나리오에서 {pharmacy['access_before']:.8f} → {pharmacy['access_after']:.8f}입니다. "
        f"병의원 시나리오 격차 점수는 {row['gap_before']:.3f} → {row['gap_after']:.3f}입니다.\n"
        "- 시행 전 확인: 실제 이동 경로, 기존 진료과·운영시간, 후보 부지·인력·예산과 주민 이용 경험을 확인하고 공간적 공급 보완 가능성을 판단합니다. "
        "시설 필요 수나 설치 권고를 산정한 결과가 아닙니다. 두 시설 유형의 변화량은 서로 다른 공급 단위에 따른 것으로 우열을 뜻하지 않습니다.\n\n"
        "[보완할 지원] 현장 확인 후 통역·예약·동행 지원 검토\n"
        "- 제안: 주민·기관 면담에서 언어·예약·이동 지원의 필요가 확인될 경우 통역 연결, 예약 도움, 이동 동행을 보완책으로 검토합니다.\n"
        "- 제안 이유: 2SFCA는 공간적 공급 접근성을 계산하며 언어 장벽이나 동행 수요를 측정하지 않습니다. 통역·동행으로 시설 공급이나 계산된 접근성이 개선된다고 해석하지 않습니다.\n"
        "- 시행 전 확인: 대상·언어·지원 경로·기존 사업·담당 기관을 현장에서 확인합니다.\n\n"
    )
    start = original.index("[우선 대응]") if "[우선 대응]" in original else original.index("[정책 후보]")
    end = original.index("[추가 확인]", start)
    return original[:start] + replacement + original[end:]


def save_siting_evidence(output_dir="outputs/facility_scenario") -> str:
    payload = build_siting_evidence()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "evidence.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frames = [pd.DataFrame(c["rows"]).assign(kind=c["kind"], threshold_km=c["threshold_km"]) for c in payload["cases"]]
    pd.concat(frames, ignore_index=True).to_csv(target / "all_regions.csv", index=False, encoding="utf-8-sig")
    cards = pd.read_parquet("data/processed/policy_cards.parquet")
    original = cards.loc[cards.adm_nm.eq(payload["selected"]["adm_nm"]), "policy_text"].iloc[0]
    (target / "policy_card.md").write_text(link_policy_card(original, payload), encoding="utf-8")
    lines = ["# 시설 추가 접근성 시나리오", "", payload["selection_reason"], "", payload["supply_definition"], "", payload["scope"], "",
             "| 시나리오 | 거리 | 지역 | 혼합 접근성 전 → 후 | 변화율 | 해당 유형 접근성 전 → 후 | 격차 전 → 후 |", "|---|---|---|---|---|---|---|"]
    for c in payload["cases"]:
        for r in c["rows"]:
            if r["adm_cd"] == payload["selected"]["adm_cd"] or r["affected"]:
                pct = "기준값 0" if r["change_pct"] is None else f"{r['change_pct']:.2f}%"
                lines.append(f"| {c['label']} | {c['threshold_km']}km | {r['adm_nm']} | {r['access_before']:.8f} → {r['access_after']:.8f} | {pct} | {r['component_before']:.8f} → {r['component_after']:.8f} | {r['gap_before']:.3f} → {r['gap_after']:.3f} |")
    lines += ["", "전체 29개 지역(변화 0 포함)은 all_regions.csv에 보관한다. 해당 유형 접근성은 병의원 또는 약국 공급만 계산한 값이며 서로 단위를 비교하지 않는다.",
              "3km에서 주변 지역의 변화가 없으면 실제 파급효과가 없다는 뜻이 아니라 대표 수요점이 가상 시설의 반경 밖에 있다는 뜻이다.",
              "", "통역·예약·동행은 공간분석에서 필요가 입증된 정책이 아니라 현장 확인 후 검토할 보완책이다. 원본 정책 카드 파일은 보존하고 선택 지역의 연결 카드를 별도로 저장한다."]
    (target / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    print(save_siting_evidence())
