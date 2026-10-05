"""src/policy/analysis_report.py 테스트.

가드레일(수치 대조·영어 단어·생성 루프)은 API 키 없이 가짜 클라이언트로 돈다.
사실 시트는 data/processed 산출물이 있어야 돈다.
"""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.policy import analysis_report as ar

DATA_AVAILABLE = all(
    Path(p).exists()
    for p in [ar.ADMIN_UNITS_PATH, ar.ACCESSIBILITY_PATH, ar.GAP_SCORES_PATH, ar.POLICY_CARDS_PATH]
)
needs_data = pytest.mark.skipif(not DATA_AVAILABLE, reason="data/processed 산출물 없음 — access/gap/policy 스테이지를 먼저 실행해야 함")


def test_numbers_from_source_pass_including_rounding_and_percent_scale():
    source = "구룡포읍 격차 점수 0.945, 외국인 1,147명, 비율 15.1%"
    text = "구룡포읍은 0.945(94.5점, 약 0.95)이고 외국인은 1,147명, 비율 15.1%다."
    assert ar.find_unverified_numbers(text, source) == []


def test_invented_number_is_flagged():
    source = "남구 평균 0.503, 북구 평균 0.457"
    text = "남구와 북구의 평균 차이는 0.046이다."
    assert ar.find_unverified_numbers(text, source) == ["0.046"]


def test_list_markers_and_small_integers_are_not_treated_as_claims():
    text = "## 1. 핵심 요약\n1. 첫째\n12. 열두째 항목\n두 구 중 2개"
    assert ar.find_unverified_numbers(text, "") == []


def test_english_word_detected_but_allowed_acronyms_pass():
    assert ar.find_english_words("이러한 pattern은 2SFCA 기준 3km에서 보인다") == ["pattern"]
    assert ar.find_english_words("2SFCA와 LLM, 3km") == []


def test_feedback_is_not_used_as_number_source():
    """회귀: 피드백에 적힌 틀린 숫자가 다음 시도의 허용 목록에 섞이면 안 된다."""
    base = ar.build_report_prompt("[분석 개요]\n- 격차 점수 0.945")
    with_fb = ar._with_feedback(base, ["입력에 없는 수치 0.046를 썼다."])
    assert "0.046" in with_fb
    assert ar.find_unverified_numbers("차이는 0.046이다.", base) == ["0.046"]


def _fake_client(contents: list[str]):
    """generate 호출과 verify 호출에 순서대로 contents를 돌려주는 가짜 OpenAI 클라이언트."""
    replies = iter(contents)

    def create(**_):
        message = SimpleNamespace(content=next(replies))
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _body(extra: str = "") -> str:
    return "\n\n".join(f"{s}\n구룡포읍의 격차 점수는 0.945로 가장 높다.{extra}" for s in ar.REPORT_SECTIONS)


@pytest.fixture
def llm_config(monkeypatch):
    config = {"model": "fake", "system_prompt": "", "report_max_tokens": 100, "temperature": 0}
    monkeypatch.setattr(ar, "load_llm_config", lambda *_: config)
    return config


def test_generate_retries_on_invented_number_then_passes(monkeypatch, llm_config):
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([_body(" 차이는 0.046이다."), _body(), "[]"]))
    result = ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945")
    assert result["attempts"] == 2
    assert result["issues"] == []
    assert "0.046" not in result["body"]


def test_generate_keeps_verifier_issues_on_last_attempt_instead_of_failing(monkeypatch, llm_config):
    issue = '[{"문장": "상위 5개는 모두 남구", "문제": "청하면은 북구"}]'
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([_body(), issue] * 3))
    result = ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945", retries=2)
    assert result["attempts"] == 3
    assert result["issues"][0]["문제"] == "청하면은 북구"


def test_unparseable_verifier_reply_is_reported_not_silently_passed(monkeypatch, llm_config):
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([_body(), "검토 결과 문제 없음"]))
    result = ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945", retries=0)
    assert len(result["issues"]) == 1
    assert "해석하지 못함" in result["issues"][0]["문제"]


def test_generate_fails_hard_when_numbers_never_verify(monkeypatch, llm_config):
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([_body(" 0.046")] * 3))
    with pytest.raises(ValueError, match="입력에 없는 수치"):
        ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945", retries=2)


@needs_data
def test_fact_sheet_precomputes_counts_llm_got_wrong():
    """회귀: 첫 실행에서 LLM이 직접 세다 틀린 항목들이 사실 시트에 미리 계산돼 있어야 한다."""
    from src.gap.score import gap_score_sensitivity

    df = ar.load_report_data()
    sheet = ar.build_fact_sheet(df, gap_score_sensitivity("보건의료"), pd.read_parquet(ar.POLICY_CARDS_PATH))
    assert len(df) == 29
    assert "[구간별 구성]" in sheet
    assert "청하면" in sheet.split("상위 5개의 소속 구:")[1].splitlines()[0]  # 상위 5위 중 북구 동
    for label in ar.CLUSTER_LABELS.values():
        assert f"- {label}: " in sheet


@needs_data
def test_assemble_report_appends_code_generated_table_and_review_box():
    df = ar.load_report_data()
    issues = [{"문장": "어떤 문장", "문제": "어떤 문제"}]
    report = ar.assemble_report(_body(), df, "fake", issues)
    assert "LLM이 자동 생성" in report
    assert "검수 필요" in report and "어떤 문제" in report
    assert report.count("| 구룡포읍 |") == 1
    assert ar.HEATMAP_REL_PATH in report


def test_gu_mismatch_catches_swapped_dong_from_real_run():
    """회귀: 두 번째 실행 실제 문장 — 청하면(북구)·오천읍(남구)을 뒤바꿔 썼다."""
    gu = {"구룡포읍": "남구", "청하면": "북구", "오천읍": "남구", "기계면": "북구"}
    text = "최우선 구간에는 남구 6개동(구룡포읍, 청하면)과 북구 2개동(오천읍, 기계면)이 포함된다."
    assert ar.find_gu_mismatches(text, gu) == ["오천읍은(는) 북구가 아니라 남구", "청하면은(는) 남구가 아니라 북구"]
    assert ar.find_gu_mismatches("남구 2개동(구룡포읍, 오천읍)과 북구 1개동(청하면)", gu) == []


def test_foreign_word_on_last_attempt_goes_to_review_box(monkeypatch, llm_config):
    """교정 호출(2번)도 단어를 못 고치면 원래 본문을 쓰고 검수 상자로 넘긴다."""
    bad = _body(" difficoltà")
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([bad, bad, bad, "[]"]))
    result = ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945", retries=0)
    assert result["issues"] == [{"문장": "difficoltà", "문제": "외국어 단어 혼입 — 한국어로 고쳐야 함"}]


def test_foreign_word_is_repaired_without_regenerating(monkeypatch, llm_config):
    """외국어 단어만 한국어로 바꾼 본문이 가드레일을 통과하면 그걸 쓰고 검수 상자에 안 남긴다."""
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([_body(" difficoltà"), _body(" 어려움이큰상황임"), "[]"]))
    result = ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945", retries=0)
    assert "difficoltà" not in result["body"]
    assert result["issues"] == []


def test_repair_is_rejected_when_it_appends_explanation_line(monkeypatch, llm_config):
    """회귀: 교정 응답 끝에 '외국어 단어가 섞였다: …' 설명 줄이 붙어 본문에 들어갔다."""
    padded = _body(" 어려움이큰상황임") + "\n위 본문에 외국어 단어가 섞였다: 곱하기"
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([_body(" difficoltà"), padded, padded, "[]"]))
    result = ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945", retries=0)
    assert "섞였다" not in result["body"]


def test_repair_is_rejected_when_it_adds_invented_number(monkeypatch, llm_config):
    broken = _body(" 어려움이큰 차이는 0.046이다.")
    monkeypatch.setattr(ar, "_client", lambda _: _fake_client([_body(" difficoltà"), broken, broken, "[]"]))
    result = ar.generate_report_body("[분석 개요]\n- 구룡포읍 0.945", retries=0)
    assert "0.046" not in result["body"]
    assert [i["문장"] for i in result["issues"]] == ["difficoltà"]


def test_review_box_renders_one_list_item_per_line():
    """회귀: 인용 블록 안 목록이 빈 인용 줄 없이 이어져 한 문단으로 뭉쳐 렌더링됐다."""
    df = pd.DataFrame(
        {"rank": [1], "adm_nm": ["구룡포읍"], "gu": ["남구"], "gap_score": [0.9], "cluster_id": [1],
         "foreign_ratio": [0.15], "access_rank": [18]}
    )
    md = ar.assemble_report("본문", df, "m", [{"문장": "a", "문제": "x"}, {"문장": "b", "문제": "y"}])
    lines = md.splitlines()
    header = next(i for i, l in enumerate(lines) if "검수 필요" in l)
    assert lines[header + 1] == ">"
    assert lines[header + 2].startswith("> - ") and lines[header + 3].startswith("> - ")


def test_clean_body_strips_prompt_labels_and_known_typos():
    text = "[구간별 구성]에 따르면 [배경 근거 1]에서 임거리 변화가 있다. 대괄호 아닌 [A] 는 그대로."
    assert ar.clean_body(text) == "구간별 구성에 따르면 배경 근거 1에서 임계거리 변화가 있다. 대괄호 아닌 [A] 는 그대로."
