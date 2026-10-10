"""저장소에 포함된 처리 데이터에서 핵심 분석을 격리된 폴더에 재생성한다.

수집·전처리·LLM 호출을 하지 않는다. 입력 스냅샷은 원본 자료의 재수집을
대체하지 않으므로, 결과 파일에 입력 해시와 원본 대비 수치 차이를 함께 기록한다.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
INPUTS = (
    "config/pipeline.yaml",
    "config/weights.yaml",
    "data/processed/admin_units.parquet",
    "data/processed/facilities.parquet",
    "data/processed/pop_centroids.parquet",
    "data/processed/policy_cards.parquet",
)
RESULTS = (
    "data/processed/accessibility.parquet",
    "data/processed/gap_scores.parquet",
    "data/processed/gap_robustness.parquet",
    "data/processed/siting_candidates.parquet",
    "data/processed/siting_plan.parquet",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare(path: str, output_dir: Path) -> dict:
    original = ROOT / path
    regenerated = output_dir / path
    if not original.exists():
        return {"status": "reference_missing"}
    before = pd.read_parquet(original)
    after = pd.read_parquet(regenerated)
    if set(before.columns) != set(after.columns) or len(before) != len(after):
        return {"status": "schema_or_row_count_changed", "before_rows": len(before), "after_rows": len(after)}
    sort_cols = [c for c in ("adm_cd", "fac_type", "threshold_km", "step") if c in before.columns]
    if sort_cols:
        before = before.sort_values(sort_cols).reset_index(drop=True)
        after = after.sort_values(sort_cols).reset_index(drop=True)
    deltas = {}
    exact_columns = []
    for column in before.columns:
        if pd.api.types.is_numeric_dtype(before[column]) and pd.api.types.is_numeric_dtype(after[column]):
            left = before[column].to_numpy(dtype=float)
            right = after[column].to_numpy(dtype=float)
            deltas[column] = float(np.nanmax(np.abs(left - right))) if len(left) else 0.0
        elif not before[column].equals(after[column]):
            exact_columns.append(column)
    return {"status": "same" if not exact_columns and all(v <= 1e-9 for v in deltas.values()) else "different",
            "max_numeric_delta": deltas, "different_other_columns": exact_columns}


def main() -> None:
    parser = argparse.ArgumentParser(description="기존 처리 입력으로 API 없이 분석 결과 재생성")
    parser.add_argument("--output-dir", required=True, type=Path, help="새 작업 폴더 (기존 파일 보호를 위해 비어 있어야 함)")
    parser.add_argument("--with-viz", action="store_true", help="PNG/HTML 지도와 대시보드도 재생성 (로컬 렌더러 필요)")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir == ROOT or ROOT in output_dir.parents and output_dir == ROOT / "data":
        parser.error("저장소의 입력 폴더를 출력 폴더로 사용할 수 없습니다")
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error(f"출력 폴더가 비어 있지 않습니다: {output_dir}")
    for rel in INPUTS:
        if not (ROOT / rel).is_file():
            parser.error(f"필수 처리 입력이 없습니다: {rel}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for rel in INPUTS:
        target = output_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)

    stages = ["access", "gap", "siting"] + (["viz"] if args.with_viz else [])
    for stage in stages:
        print(f"=== {stage} ===", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/run_pipeline.py"), "--stage", stage], cwd=output_dir, check=True)

    report = {
        "scope": "tracked processed inputs -> access, gap, siting" + (", viz" if args.with_viz else ""),
        "network_or_llm_used": False,
        "input_sha256": {rel: sha256(output_dir / rel) for rel in INPUTS},
        "output_sha256": {rel: sha256(output_dir / rel) for rel in RESULTS},
        "comparison_to_repository": {rel: compare(rel, output_dir) for rel in RESULTS},
    }
    (output_dir / "reproduction.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"재현 기록: {output_dir / 'reproduction.json'}")


if __name__ == "__main__":
    main()
