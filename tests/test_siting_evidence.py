"""추가 공급의 거리·분모·단위와 기준선 보존을 확인한다."""
import numpy as np
import pandas as pd
import pytest

from src.policy.siting_evidence import build_siting_evidence, link_policy_card, selected_case


@pytest.fixture(scope='module')
def evidence():
    return build_siting_evidence()


def test_baseline_selection_and_values_match_saved_analysis(evidence):
    assert evidence['selected']['adm_nm'] == '구룡포읍'
    case = selected_case(evidence)
    rows = pd.DataFrame(case['rows']).set_index('adm_cd')
    saved = pd.read_parquet('data/processed/accessibility.parquet')
    saved = saved[saved.fac_type.eq('보건의료')].set_index('adm_cd')
    np.testing.assert_allclose(rows.access_before, saved.loc[rows.index].access_index, rtol=0, atol=1e-14)
    gap = pd.read_parquet('data/processed/gap_scores.parquet').set_index('adm_cd')
    np.testing.assert_allclose(rows.gap_before, gap.loc[rows.index].gap_score, rtol=0, atol=1e-14)


def test_extra_supply_reaches_exactly_the_demand_points_in_catchment(evidence):
    for case in evidence['cases']:
        rows = pd.DataFrame(case['rows'])
        inside = rows.distance_km.le(case['threshold_km'])
        delta = rows.access_after - rows.access_before
        expected = np.where(inside, case['capacity_added'] / rows.loc[inside, 'pop_total'].sum(), 0)
        np.testing.assert_allclose(delta, expected, rtol=0, atol=2e-15)
        # 해당 공급 유형의 변화량은 혼합 공급의 변화량과 같고 단위는 각각 표시한다.
        np.testing.assert_allclose(rows.component_after - rows.component_before, expected, rtol=0, atol=2e-15)
        assert rows.loc[~inside, 'affected'].eq(False).all()
        assert (rows.gap_after <= rows.gap_before + 1e-12).all()
        assert len(rows) == 29 and rows.adm_cd.is_unique


def test_hospital_and_pharmacy_are_separate_added_capacity_scenarios(evidence):
    hospital = pd.DataFrame(selected_case(evidence)['rows']).set_index('adm_cd')
    pharmacy = pd.DataFrame(selected_case(evidence, 'pharmacy')['rows']).set_index('adm_cd')
    np.testing.assert_allclose(hospital.access_change, pharmacy.access_change * 3, atol=2e-15)
    assert evidence['supply_totals']['pharmacy'] == 237


def test_policy_link_preserves_facts_and_makes_complement_conditional(evidence):
    cards = pd.read_parquet('data/processed/policy_cards.parquet').set_index('adm_nm')
    original = cards.loc[evidence['selected']['adm_nm'], 'policy_text']
    result = link_policy_card(original, evidence)
    assert result.split('[우선 대응]')[0] == original.split('[우선 대응]')[0]
    assert result.split('[추가 확인]')[1] == original.split('[추가 확인]')[1]
    assert '면담에서 언어·예약·이동 지원의 필요가 확인될 경우' in result
    assert '시설 필요 수나 설치 권고를 산정한 결과가 아닙니다' in result
    assert f"{selected_case(evidence)['rows'][0]['access_after']:.8f}" in result


def test_evidence_works_from_an_input_only_reproduction_directory(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    from src.policy import siting_evidence

    root = Path.cwd()
    for name in ['config', 'data']:
        (tmp_path / name).symlink_to(root / name, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    assert not Path('src').exists()
    result = build_siting_evidence()
    assert result['selected']['adm_nm'] == '구룡포읍'
    assert result['source_sha256']['src/policy/siting_evidence.py'] == hashlib.sha256(
        Path(siting_evidence.__file__).read_bytes()).hexdigest()
