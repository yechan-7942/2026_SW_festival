"""기존 LLM 카드와 결정적 규칙 카드의 블라인드 비교 양식을 만든다."""

import argparse
import csv
import random
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.policy.context import GYEONGBUK_SERVICE_DEMAND_SCORE, GYEONGBUK_SERVICE_USAGE, build_context_block  # noqa: E402
from src.policy.report import build_dong_fact_block, load_dong_facts  # noqa: E402


def rule_card(name: str, row: pd.Series, fact: dict) -> str:
    """동일한 행정동 사실·조사 근거만 사용하는 사전 고정 규칙."""
    gap_type = str(fact.get("gap_type", ""))
    if name.endswith(("읍", "면")) and ("접근성 낮음" in gap_type):
        action = "보건소와 협의해 정기 방문 진료 가능 지역과 이동 경로를 먼저 점검합니다"
    elif "수요 높음" in gap_type:
        action = "기존 의료기관의 통역·예약 안내 수요를 조사하고 다국어 안내를 연결합니다"
    else:
        action = "기존 의료기관까지의 이동 경로와 진료 정보를 다국어로 정리합니다"
    usage = GYEONGBUK_SERVICE_USAGE["의료상담 및 진료서비스"]
    demand = GYEONGBUK_SERVICE_DEMAND_SCORE["의료상담 및 진료서비스"]
    return (
        f"포항시 {name}은 의료 접근성 격차 {int(row['rank'])}위이고 "
        f"외국인 주민은 {int(fact['pop_foreign']):,}명입니다. {action}.\n"
        f"[근거] 경북 전체 조사에서 의료상담 및 진료서비스 이용 경험은 {usage:.1%}, "
        f"요구도는 5점 척도 {demand:.2f}점입니다. 이 수치는 포항 단독 통계가 아닙니다."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="파일럿 정책 카드 블라인드 평가 양식 생성")
    parser.add_argument("--config", type=Path, default=Path("config/pilot.yaml"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/pilot_review"))
    parser.add_argument(
        "--key-path",
        type=Path,
        default=Path("data/interim/pilot_review_answer_key.csv"),
        help="평가자에게 공유하지 않을 카드 출처 키 경로 (기본값은 Git 제외 디렉터리)",
    )
    parser.add_argument("--overwrite", action="store_true", help="기존 평가 양식과 이전 위치의 정답 키를 갱신")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    names = config["admin_units"]
    if len(names) != len(set(names)):
        parser.error("파일럿 행정동이 중복되었습니다")
    output_dir = args.output_dir
    expected_files = {"review_form.csv", "answer_key.csv"}  # answer_key.csv는 이전 버전의 파일
    existing_files = {p.name for p in output_dir.iterdir()} if output_dir.exists() else set()
    if existing_files and (not args.overwrite or not existing_files <= expected_files):
        parser.error(f"출력 폴더가 비어 있지 않습니다: {output_dir}")
    if args.key_path.resolve().is_relative_to(output_dir.resolve()):
        parser.error("정답 키는 검토 양식 폴더 밖에 저장해야 합니다")

    facts = load_dong_facts()
    gap = pd.read_parquet("data/processed/gap_scores.parquet")
    gap = gap[gap["fac_type"] == "보건의료"]
    units = pd.read_parquet("data/processed/admin_units.parquet", columns=["adm_cd", "adm_nm"])
    table = gap.merge(units, on="adm_cd").set_index("adm_nm")
    cards = pd.read_parquet("data/processed/policy_cards.parquet").set_index("adm_nm")
    missing = set(names) - set(table.index) | set(names) - set(cards.index) | set(names) - set(facts)
    if missing:
        parser.error(f"처리 데이터에 없는 행정동: {sorted(missing)}")

    rng = random.Random(config["random_seed"])
    review_rows = []
    key_rows = []
    common_context = build_context_block("의료")
    for number, name in enumerate(names, start=1):
        row = table.loc[name]
        fact = facts[name]
        baseline = rule_card(name, row, fact)
        llm = cards.loc[name, "policy_text"]
        if not isinstance(llm, str) or not llm.strip():
            parser.error(f"LLM 카드가 비어 있습니다: {name}")
        llm_side = rng.choice(["A", "B"])
        review_rows.append({
            "case_id": f"P{number:02d}",
            "행정동": name,
            "공통_근거": f"{common_context}\n\n{build_dong_fact_block(name, fact)}\n- 의료 격차 순위: {int(row['rank'])}위, 점수: {float(row['gap_score']):.2f}",
            "카드_A": llm if llm_side == "A" else baseline,
            "카드_B": baseline if llm_side == "A" else llm,
            "A_근거일치_1_5": "", "B_근거일치_1_5": "",
            "A_지역적합_1_5": "", "B_지역적합_1_5": "",
            "A_실행가능_1_5": "", "B_실행가능_1_5": "",
            "선호_A_B_동률": "", "판단_근거_및_수정의견": "",
        })
        key_rows.append({"case_id": f"P{number:02d}", "행정동": name, "LLM_카드": llm_side})

    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / "review_form.csv"
    with review_path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=review_rows[0].keys())
        writer.writeheader()
        writer.writerows(review_rows)
    args.key_path.parent.mkdir(parents=True, exist_ok=True)
    with args.key_path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=key_rows[0].keys())
        writer.writeheader()
        writer.writerows(key_rows)
    legacy_key = output_dir / "answer_key.csv"
    if legacy_key.exists():
        legacy_key.unlink()
    print(f"평가 양식: {review_path}")
    print(f"정답 키(평가자에게 전달하지 않음): {args.key_path}")


if __name__ == "__main__":
    main()
