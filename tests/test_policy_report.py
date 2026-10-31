import os

import pytest
from dotenv import load_dotenv

load_dotenv()

NIM_KEY_AVAILABLE = bool(os.getenv("NVIDIA_NIM_API_KEY"))

def test_build_prompt_includes_context_and_dong_info():
    from src.policy.report import build_prompt

    prompt = build_prompt("구룡포읍", rank=1, gap_score=0.945, fac_type_label="의료")
    assert "구룡포읍" in prompt
    assert "1위" in prompt
    assert "전국다문화가족실태조사" in prompt
    assert "[근거]" in prompt


def test_load_llm_config_has_required_fields():
    from src.policy.report import load_llm_config

    config = load_llm_config()
    assert config["model"]
    assert config["base_url"].startswith("https://")
    assert config["max_tokens"] > 0


@pytest.mark.skipif(
    not NIM_KEY_AVAILABLE or os.getenv("RUN_LLM_TESTS") != "1",
    reason="실제 API 테스트는 NVIDIA_NIM_API_KEY와 RUN_LLM_TESTS=1이 필요합니다.",
)
def test_generate_policy_card_includes_evidence_line():
    from src.policy.report import generate_policy_card

    card = generate_policy_card("구룡포읍", rank=1, gap_score=0.945, fac_type_label="의료")
    assert card["adm_nm"] == "구룡포읍"
    assert "[근거]" in card["policy_text"]
