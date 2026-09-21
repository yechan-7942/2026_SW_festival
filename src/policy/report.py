import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yaml
from dotenv import load_dotenv
from openai import OpenAI

from src.policy.context import build_context_block

load_dotenv()

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
GAP_SCORES_PATH = "data/processed/gap_scores.parquet"
POLICY_CARDS_OUTPUT_PATH = "data/processed/policy_cards.parquet"
DEFAULT_CONFIG_PATH = "config/pipeline.yaml"

# gap_scores.parquet의 fac_type(facilities.parquet category_large 그대로, "보건의료")을
# 사람이 읽는 라벨(config/pipeline.yaml access.domains의 fac_type_label, "의료")로 바꾼다.
# README §1이 "의료" 단일 지수로 확정한 도메인 하나만 있어 하드코딩해도 되지만,
# 나중에 금융 등 도메인이 늘면 config에서 access.domains를 읽어 만들도록 바꿔야 한다.
FAC_TYPE_LABELS = {"보건의료": "의료"}


def load_llm_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["llm"]


def _client(llm_config: dict) -> OpenAI:
    api_key = os.getenv("NVIDIA_NIM_API_KEY")
    if not api_key:
        raise ValueError("NVIDIA_NIM_API_KEY가 .env에 설정되어 있지 않습니다.")
    return OpenAI(base_url=llm_config["base_url"], api_key=api_key)


def build_prompt(adm_nm: str, rank: int, gap_score: float, fac_type_label: str) -> str:
    """행정동 하나에 대한 프롬프트. build_context_block()의 전국 배경 근거를 그대로 붙이고,
    그 위에 이 행정동의 gap_score/rank만 얹는다 — LLM이 배경 근거 밖의 수치를 지어내지
    못하게, "위에 제공된 배경 근거 중에서만" 인용하라고 명시적으로 못박는다.
    """
    context_block = build_context_block(fac_type=fac_type_label)
    return (
        f"{context_block}\n\n"
        f"포항시 {adm_nm}은(는) {fac_type_label} 접근성 격차 점수 기준 29개 행정동 중 {rank}위"
        f"입니다(격차 점수 {gap_score:.2f}, 0~1 범위, 1에 가까울수록 격차가 심각).\n"
        f"이 행정동에 사는 외국인 주민을 위한 {fac_type_label} 접근성 정책을 2~3문장, "
        f"한국어 존댓말로 제안해줘.\n"
        f'반드시 "[근거]"로 시작하는 줄에, 위에 제공된 배경 근거 중에서만 실제로 사용한 '
        f"수치를 인용해. 배경 근거에 없는 수치는 절대 지어내지 마."
    )


def generate_policy_card(
    adm_nm: str,
    rank: int,
    gap_score: float,
    fac_type_label: str = "의료",
    config_path: str = DEFAULT_CONFIG_PATH,
) -> dict:
    """행정동 하나의 정책 카드를 생성한다.

    응답에 "[근거]" 줄이 없으면 바로 버린다(ValueError) — 근거 없이 생성된 카드를
    그대로 통과시키지 않기 위한 최소 가드레일이다. 완벽한 검증은 아니다: 모델이
    "[근거]" 줄을 넣긴 했는데 그 안의 수치가 배경 근거와 다르게 미묘히 바뀌어 있을
    가능성까지는 코드로 못 잡는다 — 사람 검수가 필요하다(README §1 메모 참고).
    """
    llm_config = load_llm_config(config_path)
    prompt = build_prompt(adm_nm, rank, gap_score, fac_type_label)
    response = _client(llm_config).chat.completions.create(
        model=llm_config["model"],
        messages=[
            {"role": "system", "content": llm_config["system_prompt"]},
            {"role": "user", "content": prompt},
        ],
        max_tokens=llm_config["max_tokens"],
        temperature=llm_config["temperature"],
    )
    content = response.choices[0].message.content.strip()
    if "[근거]" not in content:
        raise ValueError(f"{adm_nm}: 응답에 '[근거]' 줄이 없어 버림.\n{content}")

    return {
        "adm_nm": adm_nm,
        "rank": rank,
        "gap_score": gap_score,
        "fac_type_label": fac_type_label,
        "policy_text": content,
    }


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

    def _generate(row):
        fac_type_label = FAC_TYPE_LABELS.get(row.fac_type, row.fac_type)
        return generate_policy_card(row.adm_nm, row.rank, row.gap_score, fac_type_label)

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
