import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yaml
from dotenv import load_dotenv
from openai import APIStatusError, APITimeoutError, OpenAI

from src.policy.context import build_context_block

load_dotenv()

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
GAP_SCORES_PATH = "data/processed/gap_scores.parquet"
GAP_ROBUSTNESS_PATH = "data/processed/gap_robustness.parquet"
POLICY_CARDS_OUTPUT_PATH = "data/processed/policy_cards.parquet"
DEFAULT_CONFIG_PATH = "config/pipeline.yaml"

# gap_scores.parquet의 fac_type(facilities.parquet category_large 그대로, "보건의료")을
# 사람이 읽는 라벨(config/pipeline.yaml access.domains의 fac_type_label, "의료")로 바꾼다.
# README §1이 "의료" 단일 지수로 확정한 도메인 하나만 있어 하드코딩해도 되지만,
# 나중에 금융 등 도메인이 늘면 config에서 access.domains를 읽어 만들도록 바꿔야 한다.
FAC_TYPE_LABELS = {"보건의료": "의료"}

# content에서 한글이 이 비율 미만이면 정상 답변이 아니라 reasoning 누출로 간주한다.
# 정상 답변도 "[근거] 4.1%" 같은 숫자·영문 약어가 섞이니 0을 요구하진 않되, 낮게 잡는다.
MIN_HANGUL_RATIO = 0.3

# 실제로 여러 카드(호미곶면·중앙동·대송면·죽장면·두호동, 29개 중 서로 다른 실행에서
# 반복 관측)에서 모델이 "설치" 대신 한자 "設置"를 그대로 냈다. hangul_ratio 가드레일은
# 단어 하나짜리 혼입엔 거의 반응하지 않아 못 잡는다. 정확히 이 문자열로만 반복 관측된
# 결함이라 좁게 치환한다 — 범용 한자→한글 변환기를 만들면 정상적인 한자어(고유명사
# 등)까지 건드릴 위험이 있어 일부러 안 만든다.
KNOWN_HANJA_LEAKS = {"設置": "설치"}

# 실제 생성된 카드에서 관측된 결함. 장량동·해도동 카드가 "처방전 없이 약물 제공"을
# 제안했다 — 약사법상 전문의약품은 처방전 없이 줄 수 없어 정책 제안으로 부적절하다.
# 프롬프트로도 금지하지만 모델이 어길 수 있어 응답에서도 걸러 재시도한다.
# 배경 근거에 "병원/의원 처방 없이 약국 이용 16.4%" 같은 통계 라벨이 있어 단순 문자열 매칭은
# 정상 인용까지 막는다 — "처방 없이 ~ 제공/판매/지급" 형태의 제안만 잡는다.
FORBIDDEN_PATTERN = re.compile(r"처방(?:전)? ?없이[^.\n]{0,20}(?:제공|판매|지급|조제|무상|무료)")
# 29건 중 1건("외국인주원")에서 관측된 오타 — 좁게 치환한다.
KNOWN_TYPOS = {"외국인주원": "외국인 주민"}

# 조사 뒤에 공백 없이 동사가 붙는 결함("창구를설치")이 29건 중 3건에서 관측됐다.
_PARTICLE_GLUE_RE = re.compile(r"([을를])(설치|운영|배치|제공|배포|지정|마련|실시)")
_HANJA_RE = re.compile(r"[\u4e00-\u9fff]")

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# 정책 규모 표현("주 2회", "3개 언어")까지 막으면 재시도만 늘어서 작은 정수는 허용한다.
FREE_NUMBER_MAX = 10


def ungrounded_numbers(text: str, prompt: str) -> list[str]:
    """응답에 있는데 프롬프트(배경 근거 + 이 행정동 사실)에는 없는 수치. 작은 정수는 제외."""
    allowed = set(_NUMBER_RE.findall(prompt))
    bad = []
    for num in _NUMBER_RE.findall(text):
        if num in allowed:
            continue
        if "." not in num and int(num) <= FREE_NUMBER_MAX:
            continue
        bad.append(num)
    return bad


def load_dong_facts(
    admin_units_path: str = ADMIN_UNITS_PATH,
    gap_scores_path: str = GAP_SCORES_PATH,
    robustness_path: str = GAP_ROBUSTNESS_PATH,
) -> dict:
    """행정동별 사실(adm_nm → dict). 카드가 29건 모두 같은 처방이 되지 않도록 프롬프트에 넣는다."""
    units = pd.read_parquet(admin_units_path, columns=["adm_cd", "adm_nm", "pop_total", "pop_foreign"])
    units["foreign_ratio"] = units["pop_foreign"] / units["pop_total"]
    units["foreign_ratio_rank"] = units["foreign_ratio"].rank(ascending=False, method="min").astype(int)
    gap = pd.read_parquet(gap_scores_path)[["adm_cd", "rank"]]
    merged = units.merge(gap, on="adm_cd")
    try:
        rob = pd.read_parquet(robustness_path)
        rank_cols = [c for c in rob.columns if c.endswith("km_rank")]
        rob["rank_min"] = rob[rank_cols].min(axis=1)
        rob["rank_max"] = rob[rank_cols].max(axis=1)
        merged = merged.merge(rob[["adm_cd", "gap_type", "rank_min", "rank_max"]], on="adm_cd", how="left")
    except FileNotFoundError:
        pass
    return {r["adm_nm"]: r for r in merged.to_dict("records")}


def _hangul_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    hangul = sum(1 for c in letters if "가" <= c <= "힣")
    return hangul / len(letters)


def load_llm_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["llm"]


def _client(llm_config: dict) -> OpenAI:
    api_key = os.getenv("NVIDIA_NIM_API_KEY")
    if not api_key:
        raise ValueError("NVIDIA_NIM_API_KEY가 .env에 설정되어 있지 않습니다.")
    return OpenAI(base_url=llm_config["base_url"], api_key=api_key)


def _area_kind(adm_nm: str) -> str:
    # 행정구역 명칭으로 도시/농어촌 성격을 구분한다 — 읍·면은 상대적으로 넓고 시설이 드물다.
    if adm_nm.endswith("면"):
        return "면 지역(농어촌, 시설 간 거리가 멀고 대중교통이 드묾)"
    if adm_nm.endswith("읍"):
        return "읍 지역(도시와 농어촌의 중간 성격)"
    return "동 지역(시가지)"


def build_dong_fact_block(adm_nm: str, facts: dict) -> str:
    lines = [
        "[이 행정동의 사실 — 아래 수치만 이 행정동 수치로 인용할 수 있음]",
        f"- 성격: {_area_kind(adm_nm)}",
        f"- 외국인 주민 {int(facts['pop_foreign']):,}명, 전체 주민의 {facts['foreign_ratio']:.1%} "
        f"(29개 동 중 외국인 비율 {int(facts['foreign_ratio_rank'])}위)",
    ]
    if facts.get("gap_type") is not None and not pd.isna(facts.get("gap_type")):
        lines.append(f"- 격차 유형: {facts['gap_type']}")
        lo, hi = int(facts["rank_min"]), int(facts["rank_max"])
        span = f"{lo}위" if lo == hi else f"{lo}~{hi}위"
        lines.append(f"- 임계거리(1·3·5km)를 바꿔도 격차 순위는 {span} 범위")
    return "\n".join(lines)


def build_prompt(adm_nm: str, rank: int, gap_score: float, fac_type_label: str, facts: dict | None = None) -> str:
    """행정동 하나에 대한 프롬프트. build_context_block()의 전국 배경 근거를 그대로 붙이고,
    그 위에 이 행정동의 gap_score/rank와(있으면) 행정동별 사실을 얹는다 — LLM이 배경
    근거 밖의 수치를 지어내지 못하게, "위에 제공된 수치 중에서만" 인용하라고 못박는다.

    facts 없이 부르면 예전 프롬프트와 같다. 예전엔 29개 카드가 전부 "이동 보건진료소 +
    다국어 안내서"로 수렴했다 — 행정동별로 달라지는 입력이 순위·점수뿐이었기 때문이다.
    """
    context_block = build_context_block(fac_type=fac_type_label)
    fact_block = f"\n\n{build_dong_fact_block(adm_nm, facts)}" if facts else ""
    return (
        f"{context_block}{fact_block}\n\n"
        f"포항시 {adm_nm}은(는) {fac_type_label} 접근성 격차 점수 기준 29개 행정동 중 {rank}위"
        f"입니다(격차 점수 {gap_score:.2f}, 0~1 범위, 1에 가까울수록 격차가 심각).\n"
        f"이 행정동에 사는 외국인 주민을 위한 {fac_type_label} 접근성 정책을 2~3문장으로 제안해줘.\n"
        f"규칙:\n"
        f'1. 반드시 "[근거] 경북 전체 조사에 따르면 ..."처럼 "[근거]" 뒤에 같은 줄에서 이어서 쓰는 한 줄을 넣고, '
        f"위에 제공된 수치 중에서만 인용해. 제공되지 않은 수치는 절대 지어내지 마. "
        f"조사 수치는 조사 출처로, 이 행정동의 외국인 수·비율은 \"포항시 {adm_nm}의 외국인 주민 비율\"처럼 "
        f"행정동 사실로 구분해서 써(행정동 사실을 조사 결과인 것처럼 쓰지 마).\n"
        f"2. 문체는 모든 문장을 \"~합니다\" 체로 통일해(\"~하자\", \"~하라\", \"~하겠습니다\" 금지).\n"
        f"3. 이 행정동의 성격(면·읍·동)과 외국인 비율에 맞는 구체적 방법을 골라. "
        f"다른 행정동에도 그대로 붙일 수 있는 일반적인 문장(이동 진료소+다국어 안내서)만 쓰지 마.\n"
        f"4. 처방전 없이 약을 주거나 판매하는 방안, 의료법·약사법에 어긋나는 방안은 제안하지 마.\n"
        f"5. 영어 등 외국어 단어를 섞지 말고 한국어로만 써(예: bilingual 금지).\n"
        f"6. 위 경북 조사 수치는 경북 전체 집계이니 \"경북 전체 조사\"라고 밝혀서 인용해. \"경북 1권역\"이나 포항 단독 수치라고 부르지 마."
    )


def generate_policy_card(
    adm_nm: str,
    rank: int,
    gap_score: float,
    fac_type_label: str = "의료",
    config_path: str = DEFAULT_CONFIG_PATH,
    retries: int = 2,
    facts: dict | None = None,
) -> dict:
    """행정동 하나의 정책 카드를 생성한다.

    "detailed thinking off"을 줘도 가끔 reasoning이 content 필드로 그대로 새어나온다
    (실제로 29개 중 1개 — 송도동 — 에서 목격: "We need to propose..."로 시작하는 영어
    사고 과정이 문장 중간에 끊긴 채 저장됨. finish_reason도 "length"였다). "[근거]"
    문자열 포함 여부만 보는 건 부실한 가드레일이었다 — 그 사고 과정 안에 우연히
    "[근거]"라는 단어가 들어 있으면 통과해버리기 때문이다. 그래서 두 가지를 더 본다:
    (1) finish_reason이 "stop"인지(끝까지 완성됐는지), (2) content의 한글 비율이
    최소한은 되는지(reasoning 누출은 거의 다 영어라 한글 비율이 낮다). 그래도 실패하면
    retries만큼 재시도하고, 다 실패하면 ValueError로 버린다.

    hangul_ratio는 문장 전체가 새는 치명적인 경우만 잡는다 — 단어 하나짜리 한자 혼입
    (KNOWN_HANJA_LEAKS 참고)은 비율에 거의 영향이 없어 못 잡으므로 성공 시 별도로
    치환한다. 그래도 완벽한 검증은 아니다: 모델이 "[근거]" 줄을 넣긴 했는데 그 안의
    수치가 배경 근거와 다르게 미묘히 바뀌어 있을 가능성까지는 코드로 못 잡는다 —
    사람 검수가 필요하다(README §1 메모 참고).
    """
    llm_config = load_llm_config(config_path)
    prompt = build_prompt(adm_nm, rank, gap_score, fac_type_label, facts)
    client = _client(llm_config)

    last_error = None
    content = ""
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=llm_config["model"],
                messages=[
                    {"role": "system", "content": llm_config["system_prompt"]},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=llm_config["max_tokens"],
                temperature=llm_config["temperature"],
            )
        except (APIStatusError, APITimeoutError) as e:
            # 29개를 동시에 병렬 호출하면 free-tier에서 503(과부하)이 드물지 않게 난다
            # — 실제로 겪음. 콘텐츠 품질 문제와 똑같이 재시도 대상으로 취급한다.
            last_error = f"API 오류: {e}"
            time.sleep(2 * (attempt + 1))
            continue

        choice = response.choices[0]
        # reasoning 모델이라 토큰을 전부 사고 과정(reasoning_content)에 쓰고 content를
        # None으로 돌려줄 때가 있다 — 실제로 겪음. 빈 문자열로 받아 재시도 대상으로 넘긴다.
        content = (choice.message.content or "").strip()
        for hanja, hangul in KNOWN_HANJA_LEAKS.items():
            content = content.replace(hanja, hangul)
        for typo, fixed in KNOWN_TYPOS.items():
            content = content.replace(typo, fixed)
        content = _PARTICLE_GLUE_RE.sub(r"\1 \2", content)
        hangul_ratio = _hangul_ratio(content)

        if not content:
            last_error = f"content가 비어 있음(finish_reason={choice.finish_reason!r})"
        elif choice.finish_reason != "stop":
            last_error = f"finish_reason={choice.finish_reason!r} (응답이 완성되지 않음)"
        elif "[근거]" not in content:
            last_error = "'[근거]' 줄이 없음"
        elif "경북 1권역" in content:
            last_error = "경북 전체 집계 수치를 1권역 수치로 잘못 표시함"
        elif hangul_ratio < MIN_HANGUL_RATIO:
            last_error = f"한글 비율이 너무 낮음({hangul_ratio:.0%}) — reasoning 누출 의심"
        elif not re.search(r"^\s*\[근거\][^\n]{10,}", content, re.MULTILINE):
            last_error = "'[근거]' 줄에 내용이 없음 (같은 줄에 이어 써야 함)"
        elif _HANJA_RE.search(content):
            last_error = "한자 혼입"
        elif not content.rstrip().endswith("."):
            last_error = "마지막 문장이 마침표로 끝나지 않음"
        elif re.search(r"[A-Za-z]{4,}", content):
            last_error = "외국어 단어 혼입"
        elif FORBIDDEN_PATTERN.search(content):
            last_error = "처방전 없이 약을 제공하는 등 부적절한 제안 포함"
        elif ungrounded_numbers(content, prompt):
            last_error = f"프롬프트에 없는 수치 인용: {ungrounded_numbers(content, prompt)}"
        else:
            return {
                "adm_nm": adm_nm,
                "rank": rank,
                "gap_score": gap_score,
                "fac_type_label": fac_type_label,
                "policy_text": content,
            }

    raise ValueError(f"{adm_nm}: {retries + 1}번 시도 모두 실패 — {last_error}\n마지막 응답:\n{content}")


def build_policy_cards(
    admin_units_path: str = ADMIN_UNITS_PATH,
    gap_scores_path: str = GAP_SCORES_PATH,
    limit: int | None = None,
    max_workers: int = 6,
) -> pd.DataFrame:
    """29개 행정동 전체(또는 gap_score 상위 limit개)에 대해 정책 카드를 생성한다.

    행정동마다 독립된 API 호출이라 스레드풀로 병렬 실행한다 — 호출당 ~10초라
    순차로 하면 29개에 5분 가까이 걸린다. max_workers=6은 근거 있는 최적값이
    아니라 free-tier 레이트리밋을 넘지 않을 정도로 잡은 보수적인 값이다 — 429가
    나면 낮춰야 한다.

    limit을 주면 상위 limit개만 생성한다 — 스모크 테스트 시 전체를 돌리기 전에
    먼저 품질을 확인하는 용도.
    """
    admin_units = pd.read_parquet(admin_units_path, columns=["adm_cd", "adm_nm"])
    gap_scores = pd.read_parquet(gap_scores_path)
    merged = gap_scores.merge(admin_units, on="adm_cd", how="left").sort_values("rank")
    if limit is not None:
        merged = merged.head(limit)

    dong_facts = load_dong_facts(admin_units_path, gap_scores_path)

    def _generate(row):
        fac_type_label = FAC_TYPE_LABELS.get(row.fac_type, row.fac_type)
        return generate_policy_card(
            row.adm_nm, row.rank, row.gap_score, fac_type_label, facts=dong_facts.get(row.adm_nm), retries=4
        )

    rows = list(merged.itertuples())
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        cards = list(pool.map(_generate, rows))

    return pd.DataFrame(cards).sort_values("rank").reset_index(drop=True)


def save_policy_cards(path: str = POLICY_CARDS_OUTPUT_PATH, **kwargs) -> str:
    from pathlib import Path

    df = build_policy_cards(**kwargs)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


if __name__ == "__main__":
    path = save_policy_cards(limit=3)
    print(f"저장됨: {path}")
    df = pd.read_parquet(path)
    for row in df.itertuples():
        print(f"\n=== {row.adm_nm} ({row.rank}위, gap_score={row.gap_score:.3f}) ===")
        print(row.policy_text)
