"""정책 카드의 수치 인용과 경상북도 조사 범위를 점검한다.

--fix-scope-label은 원문에서 확인한 단일 범위 오류("경북 1권역")만
"경북 전체"로 바로잡는다. 문장 내용이나 수치는 재생성하지 않는다.
"""

import argparse
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.policy.report import build_prompt, load_dong_facts, ungrounded_numbers  # noqa: E402


CARDS_PATH = Path("data/processed/policy_cards.parquet")
GAP_PATH = Path("data/processed/gap_scores.parquet")
UNSAFE_SCOPE = "경북 1권역"
SAFE_SCOPE = "경북 전체"


def main() -> None:
    parser = argparse.ArgumentParser(description="정책 카드의 근거 수치·조사 범위를 점검")
    parser.add_argument("--fix-scope-label", action="store_true", help="잘못된 '경북 1권역' 표현만 '경북 전체'로 수정")
    parser.add_argument("--report", type=Path, default=Path("reports/policy_card_audit.md"))
    args = parser.parse_args()

    cards = pd.read_parquet(CARDS_PATH)
    gap = pd.read_parquet(GAP_PATH)
    gap = gap[gap["fac_type"] == "보건의료"].set_index("adm_cd")
    units = pd.read_parquet("data/processed/admin_units.parquet", columns=["adm_cd", "adm_nm"])
    name_by_code = units.set_index("adm_cd")["adm_nm"]
    facts = load_dong_facts()

    wrong_scope_before = int(cards["policy_text"].str.contains(UNSAFE_SCOPE, regex=False, na=False).sum())
    if args.fix_scope_label and wrong_scope_before:
        cards["policy_text"] = cards["policy_text"].str.replace(UNSAFE_SCOPE, SAFE_SCOPE, regex=False)
        cards.to_parquet(CARDS_PATH, index=False)

    rows = []
    for row in cards.itertuples(index=False):
        name = row.adm_nm
        text = str(row.policy_text)
        code = units.loc[units["adm_nm"] == name, "adm_cd"].iloc[0]
        rank = int(gap.loc[code, "rank"])
        score = float(gap.loc[code, "gap_score"])
        prompt = build_prompt(name, rank, score, "의료", facts[name])
        issues = []
        if UNSAFE_SCOPE in text:
            issues.append("경북 조사 범위를 1권역으로 오표기")
        bad_numbers = ungrounded_numbers(text, prompt)
        if bad_numbers:
            issues.append("프롬프트 근거에 없는 숫자: " + ", ".join(sorted(set(bad_numbers))))
        rows.append((name, issues))

    issue_rows = [(name, issue) for name, issues in rows for issue in issues]
    lines = [
        "# 정책 카드 근거 자동 점검",
        "",
        f"점검 일자: 2026-10-10  ",
        f"카드: {len(cards)}개  ",
        f"기존 카드의 잘못된 '경북 1권역' 표현: {wrong_scope_before}개  ",
        f"이번 실행에서 수정: {wrong_scope_before if args.fix_scope_label else 0}개  ",
        f"수치 근거 또는 범위 문제 잔여: {len(issue_rows)}개",
        "",
        "| 행정동 | 자동 점검 결과 |",
        "|---|---|",
    ]
    for name, issues in rows:
        lines.append(f"| {name} | {'; '.join(issues) if issues else '자동 점검 통과'} |")
    lines.extend([
        "",
        "자동 점검은 문자열 범위와 프롬프트에 없는 숫자만 확인한다. 인용한 수치의 문맥상 의미, 정책의 지역 적합성·법적 실행 가능성은 사람이 검토해야 한다.",
    ])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"카드 {len(cards)}개, 범위 오류 {wrong_scope_before}개, 수정 {wrong_scope_before if args.fix_scope_label else 0}개, 잔여 지적 {len(issue_rows)}개")
    print(f"보고서: {args.report}")


if __name__ == "__main__":
    main()
