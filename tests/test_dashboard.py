from pathlib import Path

import pytest

DATA_AVAILABLE = (
    Path("data/processed/admin_units.parquet").exists()
    and Path("data/processed/gap_scores.parquet").exists()
    and Path("data/processed/policy_cards.parquet").exists()
    and Path("outputs/figures/gap_heatmap.png").exists()
)

pytestmark = pytest.mark.skipif(
    not DATA_AVAILABLE,
    reason="admin_units/gap_scores/policy_cards.parquet 또는 gap_heatmap.png가 없음 — access/gap/policy/viz 스테이지를 먼저 실행해야 함",
)


def test_load_dashboard_data_covers_all_29_dong_with_policy_text():
    from src.viz.dashboard import load_dashboard_data

    df = load_dashboard_data("보건의료")
    assert len(df) == 29
    assert df["adm_cd"].is_unique
    assert set(df.columns) >= {"adm_cd", "adm_nm", "gap_score", "rank", "cluster_id", "policy_text"}
    assert df["policy_text"].str.len().gt(0).all()


def test_split_evidence_line_separates_근거_line():
    from src.viz.dashboard import _split_evidence_line

    body, evidence = _split_evidence_line("첫 문장입니다.\n[근거] 어떤 수치 4.1%.\n두번째 문장.")
    assert evidence.startswith("[근거]")
    assert "[근거]" not in body
    assert "첫 문장입니다." in body
    assert "두번째 문장." in body


def test_build_dashboard_html_embeds_all_dong_names_and_no_placeholder_text():
    from src.viz.dashboard import build_dashboard_html, load_dashboard_data

    html = build_dashboard_html("보건의료")
    df = load_dashboard_data("보건의료")
    for adm_nm in df["adm_nm"]:
        assert adm_nm in html
    # 예전 발표자료 목업에 남아있던 플레이스홀더 패턴이 여기 섞이면 안 된다.
    assert "Add Text Here" not in html
    assert "<script src=" not in html  # 외부 스크립트 의존 없이 완전 자체완결이어야 함


def test_save_dashboard_writes_nonempty_file(tmp_path):
    from src.viz.dashboard import save_dashboard

    path = save_dashboard(str(tmp_path / "dashboard.html"))
    assert Path(path).exists()
    assert Path(path).stat().st_size > 0
