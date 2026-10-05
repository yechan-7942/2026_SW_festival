"""파이프라인 산출물 → LLM이 서술하는 분석 보고서(outputs/analysis_report.md/.pdf).

정책 카드(src/policy/report.py)가 "행정동 하나"에 대한 처방이라면, 이 모듈은
29개 행정동 전체를 보고 "무엇이 발견됐는가"를 서술하는 보고서를 만든다.

구조는 두 층이다:
1. 사실 시트(build_fact_sheet) — parquet에서 결정론적으로 뽑은 수치만 담은 텍스트.
   LLM은 이것과 배경 근거(context.py)만 보고 쓴다.
2. 수치 대조 가드레일(find_unverified_numbers) — 정책 카드 가드레일은 형식만 봤고
   "인용된 수치가 맞는지"는 사람 검수로 넘겼다(reports/m5_policy_llm.md). 보고서는
   수치가 훨씬 많아 그 방식으론 부족하다 — 그래서 본문에 나온 모든 숫자를 프롬프트에
   실제로 들어 있던 숫자와 대조하고, 하나라도 없으면 그 숫자를 짚어 재생성시킨다.

표·지도처럼 틀리면 안 되는 부분은 LLM에 맡기지 않고 코드가 직접 붙인다
(assemble_report). LLM은 해석 서술만 한다.
"""

import json
import re
import time
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import yaml
from openai import APIStatusError, APITimeoutError

from src.gap.score import gap_score_sensitivity, load_weights
from src.policy.context import build_context_block
from src.policy.report import (
    ADMIN_UNITS_PATH,
    DEFAULT_CONFIG_PATH,
    GAP_SCORES_PATH,
    KNOWN_HANJA_LEAKS,
    MIN_HANGUL_RATIO,
    _client,
    _hangul_ratio,
    load_llm_config,
)

ACCESSIBILITY_PATH = "data/processed/accessibility.parquet"
POLICY_CARDS_PATH = "data/processed/policy_cards.parquet"
REPORT_MD_PATH = "outputs/analysis_report.md"
HEATMAP_REL_PATH = "figures/gap_heatmap.png"  # REPORT_MD_PATH 기준 상대경로

CLUSTER_LABELS = {1: "최우선", 2: "주의", 3: "보통", 4: "양호"}
TOP_N = 5  # 민감도 분석에서 "최우선 구간"으로 보는 순위 범위

# 1~10 정수는 대조 없이 허용한다 — "두 가지 이유", "세 개 구간"을 숫자로 쓰는
# 경우까지 막으면 재시도만 늘어난다. 대신 순위 1~10위를 틀리게 써도 못 잡는다
# (순위 표는 assemble_report가 코드로 붙이므로 사람이 대조할 수 있다).
FREE_INTEGERS = set(range(0, 11))

REPORT_SECTIONS = [
    "## 1. 핵심 요약",
    "## 2. 격차 분포와 주요 발견",
    "## 3. 우선 개선 대상 지역 해석",
    "## 4. 결과의 안정성",
    "## 5. 정책 시사점",
    "## 6. 한계와 유의사항",
]

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_LIST_MARKER_RE = re.compile(r"^(\s*(?:#+\s*)?)\d+\.\s", re.MULTILINE)
_LATIN_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ0-9]*")
ALLOWED_LATIN = {"2SFCA", "SFCA", "E2SFCA", "KM", "LLM", "GM"}
_GU_LIST_RE = re.compile(r"(남구|북구)[^()\n.]{0,15}\(([^)]*)\)")


def load_report_data(category_large: str = "보건의료", config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """행정동별 [adm_nm, gu, pop_total, pop_foreign, foreign_ratio, access_rank, gap_score, rank, cluster_id]."""
    with open(config_path, encoding="utf-8") as f:
        gu_by_cd = {u["adm_cd"]: u["gu"] for u in yaml.safe_load(f)["target_admin_units"]}

    admin = pd.read_parquet(ADMIN_UNITS_PATH, columns=["adm_cd", "adm_nm", "pop_total", "pop_foreign"])
    access = pd.read_parquet(ACCESSIBILITY_PATH)
    access = access[access["fac_type"] == category_large][["adm_cd", "access_index"]]
    gap = pd.read_parquet(GAP_SCORES_PATH)
    gap = gap[gap["fac_type"] == category_large][["adm_cd", "gap_score", "rank", "cluster_id"]]

    df = gap.merge(admin, on="adm_cd").merge(access, on="adm_cd")
    df["gu"] = df["adm_cd"].map(gu_by_cd)
    df["foreign_ratio"] = df["pop_foreign"] / df["pop_total"]
    # access_index 원값(0.0018 등)은 단위가 직관적이지 않아 보고서에선 순위만 쓴다.
    df["access_rank"] = df["access_index"].rank(ascending=False, method="min").astype(int)
    return df.sort_values("rank").reset_index(drop=True)


def build_fact_sheet(df: pd.DataFrame, sensitivity: pd.DataFrame, policy_cards: pd.DataFrame) -> str:
    """LLM에 넘길 사실 시트. 여기 없는 수치는 보고서에 쓸 수 없다(find_unverified_numbers)."""
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    weights = load_weights()
    thresholds = config["distance_thresholds_km"]
    n = len(df)
    total_pop, total_foreign = int(df["pop_total"].sum()), int(df["pop_foreign"].sum())

    lines = [
        "[분석 개요]",
        f"- 대상: 포항시 {n}개 행정동, 총인구 {total_pop:,}명, 외국인 주민 {total_foreign:,}명"
        f"(전체의 {total_foreign / total_pop:.1%})",
        f"- 인프라 도메인: 의료(심평원 의료기관) 단일 지수",
        f"- 접근성: 2SFCA, 기본 임계거리 {config['access']['default_threshold_km']}km",
        f"- 격차 점수 = {weights['demand_weight']} × 정규화(외국인 비율) + "
        f"{weights['access_weight']} × (1 − 정규화(접근성)), 0~1 범위, 1에 가까울수록 격차 심각",
        f"- 우선순위 구간: 순위 사분위로 최우선/주의/보통/양호 4개 구간",
        "",
        "[행정동별 결과 — 격차 점수 순]",
        "순위 | 행정동 | 구 | 격차 점수 | 구간 | 외국인 비율 | 외국인 수 | 접근성 순위(1=가장 좋음)",
    ]
    for r in df.itertuples():
        lines.append(
            f"{r.rank} | {r.adm_nm} | {r.gu} | {r.gap_score:.3f} | {CLUSTER_LABELS[r.cluster_id]} | "
            f"{r.foreign_ratio:.1%} | {r.pop_foreign:,}명 | {r.access_rank}"
        )

    # 아래 집계는 LLM이 위 표에서 직접 세게 두면 틀린다 — 첫 실행에서 "상위 5개 모두
    # 남구"(5위 청하면은 북구), "양호 구간 9개"(실제 7개)를 실제로 썼다. 세야 하는
    # 것은 전부 코드가 미리 세서 준다.
    lines += ["", "[구간별 구성]"]
    # 동 이름은 구별로 묶어서 준다 — 순위순 목록 옆에 "남구 6개, 북구 2개"만 적었더니
    # LLM이 앞 6개를 남구로 잘라 청하면(북구)·오천읍(남구)을 뒤바꿔 썼다.
    for cluster_id, g in df.groupby("cluster_id"):
        by_gu = "; ".join(f"{gu} {len(gg)}개({', '.join(gg['adm_nm'])})" for gu, gg in g.groupby("gu"))
        lines.append(f"- {CLUSTER_LABELS[cluster_id]}: {len(g)}개, {g['rank'].min()}~{g['rank'].max()}위 — {by_gu}")

    top = df.head(TOP_N)
    top_by_gu = "; ".join(
        f"{gu} {len(g)}개({', '.join(g['adm_nm'])})" for gu, g in top.groupby("gu")
    )
    worst_access = df[df["access_rank"] == df["access_rank"].max()]
    best_access = df[df["access_rank"] == 1]
    max_ratio = df.loc[df["foreign_ratio"].idxmax()]
    max_foreign = df.loc[df["pop_foreign"].idxmax()]
    lines += [
        "",
        "[주요 사실]",
        f"- 격차 점수 상위 {TOP_N}개의 소속 구: {top_by_gu}",
        f"- 접근성 순위 최하위({worst_access['access_rank'].iloc[0]}위, 동률): {', '.join(worst_access['adm_nm'])}",
        f"- 접근성 순위 1위: {', '.join(best_access['adm_nm'])}",
        f"- 외국인 비율 최고: {max_ratio['adm_nm']}({max_ratio['foreign_ratio']:.1%})",
        f"- 외국인 수 최다: {max_foreign['adm_nm']}({max_foreign['pop_foreign']:,}명)",
    ]

    lines += ["", "[구별 요약]"]
    for gu, g in df.groupby("gu"):
        top_n = int((g["cluster_id"] == 1).sum())
        lines.append(
            f"- {gu}: {len(g)}개 행정동, 평균 격차 점수 {g['gap_score'].mean():.3f}, "
            f"최우선 구간 {top_n}개, 외국인 {int(g['pop_foreign'].sum()):,}명"
        )

    merged = sensitivity.merge(df[["adm_cd", "adm_nm"]], on="adm_cd")
    rank_cols = [f"{t}km_rank" for t in thresholds]
    always_top = merged[(merged[rank_cols] <= TOP_N).all(axis=1)].sort_values(rank_cols[0])
    changed = int((merged[rank_cols].nunique(axis=1) > 1).sum())
    lines += [
        "",
        f"[임계거리 민감도 — {', '.join(f'{t}km' for t in thresholds)}로 각각 재계산]",
        f"- 임계거리에 따라 순위가 한 번이라도 바뀐 행정동: {n}개 중 {changed}개",
        f"- 모든 임계거리에서 상위 {TOP_N}위 안에 남은 행정동: {', '.join(always_top['adm_nm'])}",
        "- 기본 임계거리 상위 행정동의 임계거리별 순위:",
    ]
    default_rank_col = f"{config['access']['default_threshold_km']}km_rank"
    for _, r in merged.sort_values(default_rank_col).head(TOP_N + 3).iterrows():
        ranks = ", ".join(f"{t}km {r[f'{t}km_rank']}위" for t in thresholds)
        lines.append(f"  - {r['adm_nm']}: {ranks}")

    lines += ["", "[상위 행정동 LLM 정책 카드 요지 — 별도 생성된 카드 원문]"]
    for r in policy_cards.sort_values("rank").head(4).itertuples():
        lines.append(f"- {r.adm_nm}({r.rank}위): {' '.join(r.policy_text.split())}")

    return "\n".join(lines)


def build_report_prompt(fact_sheet: str, fac_type_label: str = "의료") -> str:
    context = build_context_block(fac_type=fac_type_label)
    sections = "\n".join(REPORT_SECTIONS)
    return (
        f"{fact_sheet}\n\n{context}\n\n"
        "위 [분석 개요]~[정책 카드 요지]는 포항시 외국인 주민 의료 인프라 격차 분석 결과이고, "
        "[배경 근거]는 공개 실태조사 통계다. 이것만 근거로 분석 보고서 본문을 한국어 '~다'체로 써줘.\n"
        f"아래 절 제목을 이 순서 그대로 쓰고, 보고서 제목과 표는 쓰지 마(코드가 따로 붙인다):\n{sections}\n"
        "규칙:\n"
        "- 수치는 위에 실제로 적힌 값만, 적힌 자릿수 그대로 쓴다. 평균·차이·비율을 새로 계산해 쓰지 마.\n"
        "- 개수·소속 구·최고/최저는 직접 세지 말고 [구간별 구성]·[주요 사실]에 적힌 대로 쓴다.\n"
        "- 어느 행정동 카드가 무엇을 제안했는지는 [정책 카드 요지]의 해당 동 카드에 있는 내용만 쓴다.\n"
        "- 수치가 없는 주장은 '경향이 보인다'처럼 해석임을 드러내고, 인과를 단정하지 마.\n"
        "- 배경 근거 수치는 포항 단독이 아니므로 '경북(권역) 조사에 따르면'처럼 출처를 밝힌다.\n"
        "- 영어 단어를 섞지 마(2SFCA 같은 고유 약어만 예외).\n"
        "- 각 절은 2~4문단, 전체 1500자 안팎."
    )


def _with_feedback(prompt: str, feedback: list[str]) -> str:
    if not feedback:
        return prompt
    return prompt + "\n\n이전 응답에 다음 문제가 있었다. 이번엔 고쳐서 다시 써:\n" + "\n".join(f"- {f}" for f in feedback)


def _to_float(token: str) -> float:
    return float(token.replace(",", ""))


def _number_variants(token: str) -> set[float]:
    """사실 시트의 숫자 하나가 본문에 나타날 수 있는 표기들.

    0.945 → 0.945/0.95/0.9/94.5/95(100점 환산), 12.3% → 12.3/12 등 반올림·환산만 허용한다.
    """
    # float round()는 0.945 → 0.94(이진 표현 오차)라 사람이 쓰는 반올림과 다르다 — Decimal로 반올림.
    x = Decimal(token.replace(",", ""))
    bases = [x]
    if "." in token and x <= 1:
        bases.append(x * 100)
    return {float(b.quantize(Decimal(q), ROUND_HALF_UP)) for b in bases for q in ("0.001", "0.01", "0.1", "1")} | {
        float(b) for b in bases
    }


def find_unverified_numbers(text: str, source: str) -> list[str]:
    """text에 있는 숫자 중 source(프롬프트)에서 유래하지 않은 것. 빈 리스트면 통과."""
    allowed: set[float] = set()
    for token in _NUMBER_RE.findall(source):
        allowed |= _number_variants(token)

    body = _LIST_MARKER_RE.sub(r"\1", text)  # "1. " 같은 목록·절 번호는 수치가 아니다
    unverified = []
    for token in _NUMBER_RE.findall(body):
        value = _to_float(token)
        if value in FREE_INTEGERS or any(abs(value - a) < 1e-9 for a in allowed):
            continue
        if token not in unverified:
            unverified.append(token)
    return unverified


_PROMPT_LABEL_RE = re.compile(r"\[([가-힣][가-힣 0-9]*)\]")
KNOWN_TYPOS = {"임거리": "임계거리"}  # 실측에서 나온 오탈자 — 같은 모델이 반복해서 낸다


def clean_body(text: str) -> str:
    """보고서에 쓰기 전 본문의 기계적 흔적을 정리한다 — 의미는 건드리지 않는다.

    프롬프트의 항목 이름이 "[구간별 구성]에 따르면"처럼 대괄호째 본문에 새어 나오는 일이
    잦아 대괄호만 벗기고, 실측에서 반복된 오탈자는 고친다.
    """
    text = _PROMPT_LABEL_RE.sub(r"\1", text)
    for typo, fixed in KNOWN_TYPOS.items():
        text = text.replace(typo, fixed)
    return text


def find_english_words(text: str) -> list[str]:
    """본문에 섞인 영어 단어(첫 실행에서 "pattern"이 그대로 들어갔다). 허용 약어는 제외."""
    return sorted({w for w in _LATIN_WORD_RE.findall(text) if w.upper() not in ALLOWED_LATIN})


def find_gu_mismatches(text: str, gu_by_name: dict[str, str]) -> list[str]:
    """"남구 6개동(구룡포읍, 청하면, …)"처럼 구 이름 바로 뒤 괄호에 나열된 동이 실제로 그 구 소속인지.

    LLM 사실 대조가 놓친 오류(첫 두 실행 모두 구 소속을 틀림)라 코드로 직접 본다.
    이 패턴 밖의 서술("청하면은 남구에 있다")은 못 잡는다.
    """
    mismatches = []
    for gu, inside in _GU_LIST_RE.findall(text):
        for name, actual in gu_by_name.items():
            if name in inside and actual != gu:
                mismatches.append(f"{name}은(는) {gu}가 아니라 {actual}")
    return sorted(set(mismatches))


def build_verify_prompt(fact_sheet: str, body: str) -> str:
    return (
        f"[사실 시트]\n{fact_sheet}\n\n[검토할 보고서 본문]\n{body}\n\n"
        "본문의 각 문장을 사실 시트와 대조해, 사실 시트와 모순되거나 사실 시트로 뒷받침되지 않는 "
        "사실 주장(개수, 소속 구, 순위, 최고/최저, 어느 동의 카드가 무엇을 제안했는지 등)을 모두 찾아라. "
        "해석·의견 문장은 제외한다. 결과는 JSON 배열로만 출력: "
        '[{"문장": "...", "문제": "..."}]. 문제가 없으면 []. '
        '"문제" 설명은 반드시 한국어로 쓴다(영어 금지).'
    )


def verify_report_body(client, llm_config: dict, fact_sheet: str, body: str, verify_retries: int = 2) -> list[dict]:
    """두 번째 LLM 호출로 본문의 사실 주장을 사실 시트와 대조한다.

    숫자 대조(find_unverified_numbers)는 "숫자가 입력에 있었는가"만 본다 — 첫 실행의
    "상위 5개는 모두 남구"처럼 숫자는 맞는데 주장이 틀린 문장은 통과했다. 이 검증은
    그런 의미 수준 오류를 잡기 위한 것이다. 같은 모델이 검증하므로 놓치는 것도,
    잘못 짚는 것도 있다(실측: 위 오류는 잡았고 일부는 놓침) — 사람 검수를 대체하지 않는다.
    응답 파싱에 실패하면 "검증 불가"를 문제 하나로 돌려줘 조용히 통과시키지 않는다.
    """
    # 추론모델이라 토큰을 reasoning에 다 쓰고 content를 비운 채 finish_reason=length로 끝나는
    # 일이 실측에서 3번 중 2번이었다 — 비었거나 끊긴 응답은 재시도하고, 한도도 넉넉히 둔다.
    content = ""
    for _ in range(verify_retries + 1):
        try:
            response = client.chat.completions.create(
                model=llm_config["model"],
                messages=[
                    {"role": "system", "content": llm_config["system_prompt"]},
                    {"role": "user", "content": build_verify_prompt(fact_sheet, body)},
                ],
                max_tokens=llm_config.get("verify_max_tokens", llm_config["report_max_tokens"]),
                temperature=0,
            )
        except (APIStatusError, APITimeoutError):
            time.sleep(2)
            continue
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if content and choice.finish_reason == "stop":
            break
    match = re.search(r"\[.*\]", content, re.DOTALL)
    try:
        issues = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        issues = None
    if not isinstance(issues, list):
        return [{"문장": "(전체)", "문제": f"검증 응답을 해석하지 못함: {content[:200]}"}]
    return [i for i in issues if isinstance(i, dict) and i.get("문장")]


def repair_foreign_words(
    client, llm_config: dict, body: str, words: list[str], source: str, gu_by_name: dict[str, str], tries: int = 2
) -> str:
    """본문에 남은 외국어 단어만 한국어로 바꾼 본문을 받아온다.

    전체를 다시 쓰게 하면 매번 다른 단어가 섞여 끝나지 않으므로(실측), 단어 치환만 시킨다.
    바뀐 본문이 원래 가드레일(절 제목·수치·구 소속·한글 비율)과 영어 단어 검사를 모두
    통과할 때만 채택하고, 못 넘으면 원래 본문을 그대로 돌려준다.
    """
    prompt = (
        f"[본문]\n{body}\n\n"
        f"위 본문에 외국어 단어가 섞였다: {', '.join(words)}\n"
        "각 단어를 문맥에 맞는 자연스러운 한국어로 바꿔 본문 전체를 다시 출력해라. "
        "그 단어 외에는 한 글자도 바꾸지 마라. 절 제목·수치·문단 구분도 그대로 둔다. "
        "설명 없이 본문만 출력한다."
    )
    for _ in range(tries):
        try:
            response = client.chat.completions.create(
                model=llm_config["model"],
                messages=[
                    {"role": "system", "content": llm_config["system_prompt"]},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=llm_config.get("verify_max_tokens", llm_config["report_max_tokens"]),
                temperature=0,
            )
        except (APIStatusError, APITimeoutError):
            time.sleep(2)
            continue
        choice = response.choices[0]
        fixed = (choice.message.content or "").strip()
        for hanja, hangul in KNOWN_HANJA_LEAKS.items():
            fixed = fixed.replace(hanja, hangul)
        # 단어만 바꾸라고 했으므로 길이·줄 수가 거의 같아야 한다 — 모델이 "외국어 단어가 섞였다: …"
        # 같은 설명 줄을 본문 끝에 덧붙인 채 가드레일을 통과한 적이 있다(실측).
        same_shape = abs(len(fixed) - len(body)) <= 0.1 * len(body) and fixed.count("\n") == body.count("\n")
        if (
            fixed
            and same_shape
            and choice.finish_reason == "stop"
            and all(s in fixed for s in REPORT_SECTIONS)
            and _hangul_ratio(fixed) >= MIN_HANGUL_RATIO
            and not find_english_words(fixed)
            and not find_unverified_numbers(fixed, source)
            and not find_gu_mismatches(fixed, gu_by_name)
        ):
            return fixed
    return body


def generate_report_body(
    fact_sheet: str,
    gu_by_name: dict[str, str] | None = None,
    config_path: str = DEFAULT_CONFIG_PATH,
    retries: int = 2,
) -> dict:
    """보고서 본문 생성.

    가드레일 두 층: (1) 형식·수치 — 완성 여부, 한글 비율, 절 제목, 입력에 없는 숫자,
    구 소속, 영어 단어. 마지막 시도까지 못 넘으면 ValueError(정책 카드와 같은 정책). (2) 사실 대조 —
    verify_report_body가 짚은 문장을 피드백으로 재생성. 마지막 시도에도 남은 지적은
    버리지 않고 issues로 돌려줘 보고서에 "검수 필요" 상자로 드러낸다 — 검증자도
    틀릴 수 있어서, 하드 실패로 보고서 전체를 막기보다 사람에게 넘기는 쪽을 택했다.
    """
    llm_config = load_llm_config(config_path)
    client = _client(llm_config)
    base_prompt = build_report_prompt(fact_sheet)

    feedback: list[str] = []
    last_error, content, issues = None, "", None
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=llm_config["model"],
                messages=[
                    {"role": "system", "content": llm_config["system_prompt"]},
                    {"role": "user", "content": _with_feedback(base_prompt, feedback)},
                ],
                max_tokens=llm_config["report_max_tokens"],
                temperature=llm_config["temperature"],
            )
        except (APIStatusError, APITimeoutError) as e:
            last_error = f"API 오류: {e}"
            time.sleep(2 * (attempt + 1))
            continue

        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        for hanja, hangul in KNOWN_HANJA_LEAKS.items():
            content = content.replace(hanja, hangul)
        content = clean_body(content)
        missing_sections = [s for s in REPORT_SECTIONS if s not in content]

        # 숫자 대조는 피드백을 뺀 base_prompt 기준 — 피드백에 적힌 "틀린 숫자"가
        # 다음 시도에서 허용 목록에 섞여 들어가지 않게 한다.
        if choice.finish_reason != "stop":
            last_error = f"finish_reason={choice.finish_reason!r} (응답이 완성되지 않음)"
            feedback = []
        elif _hangul_ratio(content) < MIN_HANGUL_RATIO:
            last_error = f"한글 비율이 너무 낮음({_hangul_ratio(content):.0%}) — reasoning 누출 의심"
            feedback = []
        elif missing_sections:
            last_error = f"절 제목 누락: {missing_sections}"
            feedback = [f"절 제목 {', '.join(missing_sections)}가 빠졌다. 지정한 절 제목을 그대로 써라."]
        elif numbers := find_unverified_numbers(content, base_prompt):
            last_error = f"입력에 없는 수치: {numbers}"
            feedback = [f"입력에 없는 수치 {', '.join(numbers)}를 썼다. 새로 계산하거나 지어낸 수치다 — 위에 적힌 수치만 써라."]
        elif gu_errors := find_gu_mismatches(content, gu_by_name or {}):
            last_error = f"구 소속 오류: {gu_errors}"
            feedback = [f"구 소속을 틀렸다: {', '.join(gu_errors)}. [구간별 구성]의 구별 목록을 그대로 따라라."]
        elif (words := find_english_words(content)) and attempt < retries:
            last_error = f"외국어 단어 혼입: {words}"
            feedback = [f"외국어 단어 {', '.join(words)}를 썼다. 한국어로 바꿔라."]
        else:
            # 외국어 단어는 마지막 시도에선 실패 대신 검수 상자로 넘긴다 — 실측에서 매 시도마다
            # 다른 단어("pattern", "difficoltà")가 섞여 하드 실패로는 보고서가 안 나왔다.
            if words:
                content = repair_foreign_words(client, llm_config, content, words, base_prompt, gu_by_name or {})
                words = find_english_words(content)
            # 보고서 본문은 [배경 근거]도 보고 썼으므로 검증에도 같이 줘야 그 수치를 오탐하지 않는다
            verify_sheet = f"{fact_sheet}\n\n{build_context_block(fac_type='의료')}"
            issues = verify_report_body(client, llm_config, verify_sheet, content)
            issues += [{"문장": w, "문제": "외국어 단어 혼입 — 한국어로 고쳐야 함"} for w in words]
            if not issues or attempt == retries:
                return {"body": content, "attempts": attempt + 1, "model": llm_config["model"], "issues": issues}
            last_error = f"사실 대조 지적 {len(issues)}건"
            feedback = [f"\"{i['문장']}\" — {i.get('문제', '')}" for i in issues]

    raise ValueError(f"분석 보고서: {retries + 1}번 시도 모두 실패 — {last_error}\n마지막 응답:\n{content}")


def assemble_report(
    body: str, df: pd.DataFrame, model: str, issues: list[dict] | None = None, generated_on: date | None = None
) -> str:
    """LLM 본문 앞뒤에 코드가 만든 머리말·표·지도를 붙인다 — 표와 지도는 LLM을 거치지 않는다."""
    generated_on = generated_on or date.today()
    table = ["| 순위 | 행정동 | 구 | 격차 점수 | 구간 | 외국인 비율 | 접근성 순위 |", "|---|---|---|---|---|---|---|"]
    for r in df.itertuples():
        table.append(
            f"| {r.rank} | {r.adm_nm} | {r.gu} | {r.gap_score:.3f} | {CLUSTER_LABELS[r.cluster_id]} | "
            f"{r.foreign_ratio:.1%} | {r.access_rank} |"
        )
    review_box = []
    if issues:
        # 인용 블록 안의 목록은 목록 앞에 빈 인용 줄(">")이 있어야 한 줄씩 나뉘어 렌더링된다
        review_box = ["", "> ⚠️ **검수 필요** — 자동 사실 대조에서 아래 문장이 지적됐다(검증자도 틀릴 수 있음):", ">"]
        review_box += [f"> - \"{i['문장'].strip()}\" — {str(i.get('문제', '')).strip()}" for i in issues]
    return "\n".join(
        [
            "# 포항시 외국인 주민 의료 인프라 격차 분석 보고서",
            "",
            f"생성일 {generated_on.isoformat()} · 본문 생성 모델 `{model}` · `scripts/run_pipeline.py --stage report`",
            "",
            "> 이 보고서의 **본문(1~6절)은 LLM이 자동 생성**했다. 본문의 모든 수치는 파이프라인 산출물·"
            "공개 실태조사 수치와 코드로 대조해 통과한 것만 남겼고(1~10의 정수는 대조 제외), "
            "사실 주장은 별도 LLM 호출로 사실 시트와 한 번 더 대조했다. 그래도 "
            "해석과 정책 제안의 타당성은 사람이 검수해야 한다. 아래 부록의 표와 지도는 LLM을 거치지 않았다.",
            *review_box,
            "",
            body,
            "",
            "---",
            "",
            # 제목만 앞 쪽 아래에 남고 표가 다음 쪽으로 넘어가지 않도록 부록은 새 쪽에서 시작한다
            '<div style="page-break-before: always"></div>',
            "",
            "## 부록 A. 행정동별 격차 점수 (코드 생성)",
            "",
            *table,
            "",
            "## 부록 B. 격차 점수 지도",
            "",
            f"![포항시 행정동별 의료 접근성 격차 점수]({HEATMAP_REL_PATH})",
            "",
        ]
    )


def save_analysis_report(path: str = REPORT_MD_PATH, category_large: str = "보건의료") -> str:
    df = load_report_data(category_large)
    sensitivity = gap_score_sensitivity(category_large)
    policy_cards = pd.read_parquet(POLICY_CARDS_PATH)
    fact_sheet = build_fact_sheet(df, sensitivity, policy_cards)

    result = generate_report_body(fact_sheet, gu_by_name=dict(zip(df["adm_nm"], df["gu"])))
    report = assemble_report(result["body"], df, result["model"], result["issues"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(report, encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"저장됨: {save_analysis_report()}")
