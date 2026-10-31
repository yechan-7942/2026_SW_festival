import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import yaml
from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI

from src.gap.score import TOP_N, min_max_normalize
from src.policy.context import build_context_block

load_dotenv()

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
GAP_SCORES_PATH = "data/processed/gap_scores.parquet"
GAP_ROBUSTNESS_PATH = "data/processed/gap_robustness.parquet"
ACCESSIBILITY_PATH = "data/processed/accessibility.parquet"
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
KNOWN_TYPOS = {
    "외국인주원": "외국인 주민",
    "필요가 합니다": "필요가 있습니다",
    "경우가 확인되는 경우": "것으로 확인되는 경우",
    "우선 의미가 합니다": "우선 검토할 필요가 있습니다",
}

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
    accessibility_path: str = ACCESSIBILITY_PATH,
    fac_type: str = "보건의료",
) -> dict:
    """행정동별 사실(adm_nm → dict). 카드가 29건 모두 같은 처방이 되지 않도록 프롬프트에 넣는다."""
    units = pd.read_parquet(admin_units_path, columns=["adm_cd", "adm_nm", "pop_total", "pop_foreign"])
    units["foreign_ratio"] = units["pop_foreign"] / units["pop_total"]
    units["foreign_ratio_rank"] = units["foreign_ratio"].rank(ascending=False, method="min").astype(int)
    gap = pd.read_parquet(gap_scores_path)
    gap = gap.loc[gap["fac_type"] == fac_type, ["adm_cd", "rank"]]
    merged = units.merge(gap, on="adm_cd")
    access = pd.read_parquet(accessibility_path)
    access = access.loc[access["fac_type"] == fac_type, ["adm_cd", "access_index"]].copy()
    if set(units["adm_cd"]) != set(gap["adm_cd"]) or set(gap["adm_cd"]) != set(access["adm_cd"]):
        raise ValueError("행정동·접근성·격차 결과의 행정동 목록이 일치하지 않습니다.")
    access["access_norm"] = min_max_normalize(access["access_index"])
    access["access_rank"] = access["access_index"].rank(ascending=False, method="min").astype(int)
    merged = merged.merge(access, on="adm_cd", validate="one_to_one")
    if len(merged) != len(gap):
        raise ValueError("행정동·접근성·격차 결과의 행정동 목록이 일치하지 않습니다.")
    merged["n_units"] = len(merged)
    try:
        rob = pd.read_parquet(robustness_path)
        rob = rob.loc[rob["fac_type"] == fac_type].copy()
        rank_cols = [c for c in rob.columns if c.endswith("km_rank")]
        rob["rank_min"] = rob[rank_cols].min(axis=1)
        rob["rank_max"] = rob[rank_cols].max(axis=1)
        merged = merged.merge(
            rob[["adm_cd", "gap_type", "rank_min", "rank_max"]], on="adm_cd", how="left", validate="one_to_one"
        )
        merged["rank_thresholds"] = "·".join(c.removesuffix("km_rank") for c in rank_cols)
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
    # 명칭으로 확인되는 행정구역 종류만 사용한다. 교통·시설 분포는 추정하지 않는다.
    if adm_nm.endswith("면"):
        return "면 지역"
    if adm_nm.endswith("읍"):
        return "읍 지역"
    return "동 지역"


def build_dong_fact_block(adm_nm: str, facts: dict) -> str:
    lines = [
        "[이 행정동의 사실 — 아래 수치만 이 행정동 수치로 인용할 수 있음]",
        f"- 행정구역 종류: {_area_kind(adm_nm)}",
        f"- 외국인 주민 {int(facts['pop_foreign']):,}명, 전체 주민의 {facts['foreign_ratio']:.1%} "
        f"(분석 {int(facts.get('n_units', 29))}개 지역 중 외국인 비율 {int(facts['foreign_ratio_rank'])}위)",
    ]
    if "access_index" in facts:
        lines.append(
            f"- 공간 접근성 지수 {facts['access_index']:.6f}, 지역 간 정규화값 {facts['access_norm']:.3f} "
            f"(높을수록 접근성 높음, 접근성 순위 {int(facts['access_rank'])}위)"
        )
    if facts.get("gap_type") is not None and not pd.isna(facts.get("gap_type")):
        lines.append(f"- 격차 유형: {facts['gap_type']} (분석 지역 내 상대 분류)")
        lo, hi = int(facts["rank_min"]), int(facts["rank_max"])
        span = f"{lo}위" if lo == hi else f"{lo}~{hi}위"
        lines.append(f"- 임계거리({facts.get('rank_thresholds', '1·3·5')}km)별 격차 순위 범위: {span}")
    return "\n".join(lines)


def build_prompt(adm_nm: str, rank: int, gap_score: float, fac_type_label: str, facts: dict | None = None) -> str:
    """지역 사실에 근거한 우선 대응과 보완 지원을 구체적인 정책 초안으로 요청한다."""
    context_block = build_context_block(fac_type=fac_type_label)
    fact_block = f"\n\n{build_dong_fact_block(adm_nm, facts)}" if facts else ""
    return (
        f"{context_block}{fact_block}\n\n"
        f"포항시 {adm_nm}: {fac_type_label} 격차 순위 {rank}위, 점수 {gap_score:.3f}.\n"
        "당신은 확정 처방이 아닌 현장 검토용 정책 카드의 초안을 작성합니다.\n"
        "분석의 한계:\n"
        "- 격차는 외국인 비율(수요 대리지표)과 공간 접근성 결핍의 가중합입니다. 순위는 교통·언어 장벽의 원인 분석이 아닙니다.\n"
        "- 공간 접근성은 전체 주민을 분모로 한 거리 기반 모형이며, 실제 이동시간·교통편·진료 이용률이 아닙니다.\n"
        "- 수요 높음은 외국인 비율의 상대 평가이며 실제 진료 수요가 많다는 의미가 아닙니다. 양호형도 장벽이 없다는 뜻은 아닙니다.\n"
        "- 읍·면·동 명칭만으로 교통 부족, 산업·어업 종사, 언어·비용 문제를 지역 사실로 단정하지 마세요.\n"
        "- 경북 전체·전국 조사는 배경 참고입니다. 그 수치나 응답 원인을 해당 지역에 그대로 적용하지 마세요.\n"
        "작성 규칙:\n"
        "- 해석은 상대적 격차 유형, 접근성, 외국인 비율이 현장 검토에 주는 의미를 한 문장으로 작성합니다.\n"
        "- 순위·임계거리·안정성에 대한 해석은 코드가 표시하므로 응답에서 언급하지 마세요.\n"
        "- 정책 후보는 정확히 두 개이며 배열 순서대로 '우선 대응', '보완할 지원'입니다. 무엇부터 대응하고 어떤 지원을 추가·보완할지 제안합니다.\n"
        "- 각 후보는 제목(title), 구체적 조치(action), 지역 분석과 연결한 제안 이유(reason), 시행 범위를 정할 확인 조건(condition)을 포함합니다.\n"
        "- 우선 대응은 해당 유형에서 먼저 다룰 과제와 실행안을, 보완할 지원은 첫 조치를 돕는 서로 다른 지원을 제안합니다. 순서는 정책 검토 제안이며 효과나 시급성이 입증된 순위로 단정하지 마세요.\n"
        "- 복합 취약형은 외국인 주민의 의료기관 이용 경로·예약 지원을 묶은 시범안을 우선 제안하고, 보완안은 언어 등 별도 장벽이 확인될 때 추가하는 지원으로 구분합니다.\n"
        "- 접근성 부족형은 외국인 비율이 낮다는 이유로 지원을 배제하지 않되, 주민 공통의 이동 경로·진료 시간 정보 제공을 우선 제안합니다. 보완안은 경로 정보만으로 방문하기 어려운 이용자에 대한 개별 연결 지원으로 구분합니다.\n"
        "- 수요 집중형은 이용 안내와 예약 문의를 기존 창구에 모으는 조치를, 보완안은 확인된 상담·예약 처리 부담에 대응하는 지원으로 구분합니다.\n"
        "- 양호형은 기존 안내 자료와 문의 경로의 정비를 우선 제안합니다. 보완안은 안내만으로 이용하기 어려운 주민에 대한 개별 도움으로 한정하며, 일률적인 상시 통역·동행·신규 전용 창구를 제안하지 마세요.\n"
        "- 우선안에 보완안의 서비스를 미리 포함하지 마세요. 예를 들어 우선안이 경로 안내라면 보완안은 같은 안내·예약의 반복이 아닌 개별 이용 지원으로 씁니다. 언어 지원 필요는 별도 확인할 가설이며 지역 사실로 쓰지 마세요.\n"
        "- reason에서 '수요 높음·낮음'은 반드시 외국인 비율의 상대 평가라는 뜻으로 표현하고 진료 수요나 인구 규모의 부족·과다로 바꾸지 마세요.\n"
        "- action에는 지원 대상과 제공할 서비스를 구체적으로 씁니다. '현황 확인', '현장 점검', '기관 연계 검토', '장벽 완화'만으로 끝내지 말고 예약 도움·이용 안내·통역 연결 등 실제 지원 내용을 제시합니다.\n"
        "- 기존 서비스가 부족하거나 없다고 단정하지 말고 추가·보완 제안으로 표현합니다. 조치 자체를 제안하고 확인 조건은 시행 대상·방식·확대 여부를 정하는 데 사용합니다.\n"
        "- condition은 '어떤 문제가 확인되는 경우'라는 선택 기준이며 반드시 '경우'로 끝납니다. '현황을 조사합니다' 같은 조사 행위는 조건이 아닙니다.\n"
        "- 미확인 기관·시설·사업이 존재하거나 특정 위치에 있다고 단정하지 마세요. 신규 시설·순회 진료·셔틀은 필요성과 기존 사업을 확인하기 전 확정하지 마세요.\n"
        "- 외국인 전용 서비스를 일률적으로 제안하지 말고 기존 지역 서비스와의 연계를 먼저 검토합니다. 처방전 없이 약을 제공하는 제안은 금지합니다.\n"
        "- 예산·횟수·효과·인력 수를 만들지 마세요. 수치와 [근거]는 코드가 표시하므로 응답에는 숫자를 쓰지 마세요.\n"
        "- 추가 확인은 실제로 조사할 항목을 두 개에서 네 개로 작성하고 정책 조건과 연결합니다.\n"
        "- 한국어만 사용하고 서술문은 ~합니다 체로 씁니다. 각 항목은 짧게, 전체 한국어 본문은 약 육백 자로 씁니다.\n"
        '아래 구조의 JSON 객체만 반환하세요. 코드 블록이나 설명을 붙이지 마세요.\n'
        '{"interpretation":"지역 분석 해석", "policy_options":['
        '{"title":"우선 대응할 정책명", "action":"대상과 구체적인 지원 내용을 제안합니다.", "reason":"지역 분석에 근거한 제안 이유입니다.", "condition":"시행 대상과 방식을 결정할 필요가 확인되는 경우"},'
        '{"title":"추가로 보완할 정책명", "action":"첫 조치를 보완할 지원 내용을 제안합니다.", "reason":"이 지원을 함께 검토할 이유입니다.", "condition":"보완 지원의 필요가 확인되는 경우"}], '
        '"checks":["확인할 항목", "확인할 항목"]}'
    )


def parse_policy_response(content: str) -> dict:
    """형식·빈 항목·수치 재서술을 검증한다. 정책 타당성 판단은 사람의 몫이다."""
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"interpretation", "policy_options", "checks"}:
        raise ValueError("해석·정책 후보·추가 확인 JSON 구조가 아닙니다.")

    def require_text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValueError("빈 항목 또는 너무 긴 항목입니다.")
        if "\n" in value or "[" in value or "]" in value:
            raise ValueError("항목 안의 줄바꿈·별도 구획은 허용하지 않습니다.")
        return value.strip()

    payload["interpretation"] = require_text(payload["interpretation"])
    if re.search(r"순위|임계거리|안정성|안정적|불안정", payload["interpretation"]):
        raise ValueError("순위·거리별 안정성 설명은 코드가 표시하므로 해석에서 제외하세요.")
    options = payload["policy_options"]
    if not isinstance(options, list) or len(options) != 2:
        raise ValueError("우선 대응과 보완할 지원을 각각 한 개씩 제안해야 합니다.")
    for option in options:
        if not isinstance(option, dict) or set(option) != {"title", "action", "reason", "condition"}:
            raise ValueError("정책 후보에 제목·구체적 조치·제안 이유·확인 조건이 필요합니다.")
        for key in option:
            option[key] = require_text(option[key])
        if not option["condition"].endswith("경우"):
            raise ValueError("확인 조건은 조사 행위가 아닌 '문제가 확인되는 경우' 형태여야 합니다.")
    checks = payload["checks"]
    if not isinstance(checks, list) or not 2 <= len(checks) <= 4:
        raise ValueError("추가 확인 항목은 두 개에서 네 개여야 합니다.")
    payload["checks"] = [require_text(check) for check in checks]
    text = " ".join([payload["interpretation"], *[v for o in options for v in o.values()], *payload["checks"]])
    if _NUMBER_RE.search(text):
        raise ValueError("수치는 코드가 표시합니다. 생성 서술에 숫자가 포함됐습니다.")
    if re.search(r"[A-Za-z]", text) or _HANJA_RE.search(text) or _hangul_ratio(text) < MIN_HANGUL_RATIO:
        raise ValueError("한국어 이외 단어 또는 사고 과정 누출이 포함됐습니다.")
    if FORBIDDEN_PATTERN.search(text):
        raise ValueError("처방전 없이 약을 제공하는 등 부적절한 제안입니다.")
    return payload


def validate_policy_facts(payload: dict, facts: dict | None) -> None:
    """관측된 유형·방향 반전만 검출한다. 자유 문장의 의미 전체를 검증하지는 않는다."""
    if not facts or not isinstance(facts.get("gap_type"), str):
        return
    gap_type = facts["gap_type"]
    text = "\n".join([payload["interpretation"], *[o["reason"] for o in payload.get("policy_options", [])]])
    expected_type = gap_type.split("(", 1)[0]
    for label in ("복합 취약형", "접근성 부족형", "수요 집중형", "양호형"):
        if label in text and label != expected_type:
            raise ValueError(f"해석·제안 이유의 격차 유형이 입력 유형({expected_type})과 다릅니다.")
    modifiers = r"\s*(?:(?:이|가|은|는|도|상대적으로|상대적|비교적|분석 지역 중)\s*)*"
    demand = r"(?:외국인\s*(?:주민\s*)?비율|수요)" + modifiers
    access = r"접근성" + modifiers
    wrong_demand = "낮" if "수요 높음" in gap_type else "높"
    wrong_access = r"(?:높|양호|좋)" if "접근성 낮음" in gap_type else r"(?:낮|부족|취약)"
    if re.search(demand + wrong_demand, text):
        raise ValueError(f"해석·제안 이유의 외국인 비율·수요 방향이 입력 유형({gap_type})과 반대입니다.")
    if re.search(access + wrong_access, text):
        raise ValueError(f"해석·제안 이유의 공간 접근성 방향이 입력 유형({gap_type})과 반대입니다.")


def render_policy_card(adm_nm: str, rank: int, gap_score: float, payload: dict, facts: dict | None) -> str:
    """수치·출처·한계는 코드로 고정하고 생성 서술은 별도 구획으로 표시한다."""
    factual = f"{adm_nm}의 격차 점수는 {gap_score:.3f}, 격차 순위는 {rank}위입니다."
    if facts:
        factual += "\n" + "\n".join(build_dong_fact_block(adm_nm, facts).splitlines()[1:])
    stability = ""
    if facts and pd.notna(facts.get("rank_max")):
        lo, hi = int(facts["rank_min"]), int(facts["rank_max"])
        if hi <= TOP_N:
            stability = f"검토한 모든 임계거리에서 격차 상위 {TOP_N}위 이내여서 우선 현장 검토 대상으로 볼 수 있습니다. "
        elif lo <= TOP_N:
            stability = f"거리 설정에 따라 격차 상위 {TOP_N}위 진입 여부가 달라져 설정과 현장 이용 경로를 함께 확인해야 합니다. "
        else:
            stability = f"검토한 임계거리에서는 격차 상위 {TOP_N}위 밖입니다. 낮은 점수만으로 개별 이용 장벽이 없다고 판단하지 않습니다. "
    options = "\n\n".join(
        f"[{label}] {o['title']}\n"
        f"- 제안: {o['action']}\n"
        f"- 제안 이유: {o['reason']}\n"
        f"- 시행·확대 전 확인 조건: {o['condition']}"
        for label, o in zip(("우선 대응", "보완할 지원"), payload["policy_options"])
    )
    return (
        f"[확인된 사실] {factual}\n\n"
        f"[해석] {stability}{payload['interpretation']}\n"
        "이 분석만으로 실제 이동·언어·비용 장벽이나 진료 수요를 확정할 수 없습니다.\n\n"
        "제안 순서는 분석을 바탕으로 한 검토 순서이며 정책 효과가 입증된 순위는 아닙니다.\n\n"
        f"{options}\n\n"
        "[추가 확인]\n" + "\n".join(f"- {c}" for c in payload["checks"]) + "\n\n"
        "[근거] 지역 수치는 KOSIS 인구 자료와 2SFCA 공간분석 결과입니다. 경북 전체·전국 조사는 배경 참고이며 지역별 원인 자료가 아닙니다."
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
    """우선 대응·보완 지원 JSON을 검증한 뒤 코드의 사실 구획과 합친다."""
    llm_config = load_llm_config(config_path)
    prompt = build_prompt(adm_nm, rank, gap_score, fac_type_label, facts)
    client = _client(llm_config).with_options(
        timeout=llm_config.get("policy_timeout_seconds", 90), max_retries=0
    )
    last_error = None
    content = ""
    for attempt in range(retries + 1):
        feedback = f"\n이전 응답 검증 실패: {last_error} 구조와 작성 규칙을 지켜 다시 작성하세요." if last_error else ""
        try:
            response = client.chat.completions.create(
                model=llm_config["model"],
                messages=[
                    {"role": "system", "content": llm_config["system_prompt"]},
                    {"role": "user", "content": prompt + feedback},
                ],
                max_tokens=llm_config["max_tokens"],
                temperature=llm_config["temperature"],
            )
        except APIStatusError as e:
            if e.status_code < 500 and e.status_code not in {408, 409, 429}:
                raise RuntimeError(
                    f"정책 모델 {llm_config['model']} 호출 실패(HTTP {e.status_code}). "
                    "config/pipeline.yaml의 모델 및 계정 접근 권한을 확인하세요."
                ) from e
            last_error = f"일시적 API 오류(HTTP {e.status_code})"
        except APIConnectionError:
            last_error = "API 연결 실패 또는 응답 시간 초과"
        else:
            choice = response.choices[0]
            content = (choice.message.content or "").strip()
            for before, after in {**KNOWN_HANJA_LEAKS, **KNOWN_TYPOS}.items():
                content = content.replace(before, after)
            content = _PARTICLE_GLUE_RE.sub(r"\1 \2", content)
            try:
                if choice.finish_reason != "stop":
                    raise ValueError(f"미완성 응답(finish_reason={choice.finish_reason})")
                payload = parse_policy_response(content)
                validate_policy_facts(payload, facts)
            except (ValueError, TypeError) as e:
                last_error = str(e)
            else:
                return {
                    "adm_nm": adm_nm,
                    "rank": rank,
                    "gap_score": gap_score,
                    "fac_type_label": fac_type_label,
                    "policy_text": render_policy_card(adm_nm, rank, gap_score, payload, facts),
                    "policy_json": json.dumps(payload, ensure_ascii=False),
                    "card_version": "action-plan-v2",
                    "model": llm_config["model"],
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise ValueError(f"{adm_nm}: {retries + 1}번 시도 모두 실패 — {last_error}")


def build_policy_cards(
    admin_units_path: str = ADMIN_UNITS_PATH,
    gap_scores_path: str = GAP_SCORES_PATH,
    limit: int | None = None,
    max_workers: int | None = None,
    config_path: str = DEFAULT_CONFIG_PATH,
    accessibility_path: str = ACCESSIBILITY_PATH,
    robustness_path: str = GAP_ROBUSTNESS_PATH,
) -> pd.DataFrame:
    """29개 행정동 전체(또는 gap_score 상위 limit개)에 대해 정책 카드를 생성한다.

    행정동마다 독립된 API 호출이라 스레드풀로 병렬 실행한다. 기본 동시 호출 수는
    llm.policy_max_workers에서 읽는다. 실제 6개 동시 호출에서 500·시간 초과가
    발생해 기본값을 낮췄다. 인자로 주면 해당 실행에서만 재정의한다.

    limit을 주면 상위 limit개만 생성한다 — 스모크 테스트 시 전체를 돌리기 전에
    먼저 품질을 확인하는 용도.
    """
    if max_workers is None:
        max_workers = load_llm_config(config_path).get("policy_max_workers", 2)
    admin_units = pd.read_parquet(admin_units_path, columns=["adm_cd", "adm_nm"])
    gap_scores = pd.read_parquet(gap_scores_path)
    merged = gap_scores.merge(admin_units, on="adm_cd", how="left").sort_values("rank")
    if limit is not None:
        merged = merged.head(limit)

    facts_by_domain = {
        fac_type: load_dong_facts(
            admin_units_path, gap_scores_path, robustness_path, accessibility_path, fac_type
        ) for fac_type in merged["fac_type"].unique()
    }

    def _generate(row):
        fac_type_label = FAC_TYPE_LABELS.get(row.fac_type, row.fac_type)
        return generate_policy_card(
            row.adm_nm, row.rank, row.gap_score, fac_type_label,
            config_path=config_path, facts=facts_by_domain[row.fac_type][row.adm_nm], retries=2
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
    path = save_policy_cards(path="outputs/policy_cards_sample.parquet", limit=3)
    print(f"저장됨: {path}")
    df = pd.read_parquet(path)
    for row in df.itertuples():
        print(f"\n=== {row.adm_nm} ({row.rank}위, gap_score={row.gap_score:.3f}) ===")
        print(row.policy_text)
