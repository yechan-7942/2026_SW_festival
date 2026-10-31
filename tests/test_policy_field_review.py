"""현장 검토 카드의 사실 분리와 조건 누락을 검증하는 회귀 테스트."""
import json
from types import SimpleNamespace

try:
    import httpx
except ImportError:
    import httpx2 as httpx
import pandas as pd
import pytest
from openai import NotFoundError

from src.policy import report as policy


def payload():
    return {
        'interpretation': '외국인 비율이 상대적으로 높고 공간 접근성이 낮아 현장 검토를 우선합니다.',
        'policy_options': [
            {'title': '진료 이용 연결 지원', 'condition': '실제 이동 경로의 불편이 확인되는 경우',
             'action': '외국인 주민에게 진료 예약과 이동 경로 안내를 함께 지원하는 방안을 제안합니다.',
             'reason': '공간 접근성이 낮아 의료기관 이용을 연결하는 지원을 먼저 제안합니다.'},
            {'title': '다국어 이용 안내 보완', 'condition': '언어로 인한 이용 어려움이 확인되는 경우',
             'action': '외국인 주민에게 다국어 진료 절차 안내와 통역 연결을 제공하는 방안을 제안합니다.',
             'reason': '의료 이용 연결 지원을 실제 진료 절차 이해까지 보완하기 위해 제안합니다.'},
        ],
        'checks': ['실제 의료기관 이동시간', '기존 지원사업과 이용 가능 조건'],
    }


def test_prompt_requests_concrete_prioritized_support_without_claiming_missing_services():
    prompt = policy.build_prompt('구룡포읍', 1, 0.883, '의료')
    assert "배열 순서대로 '우선 대응', '보완할 지원'" in prompt
    assert '지원 대상과 제공할 서비스를 구체적으로' in prompt
    assert '부족하거나 없다고 단정하지 말고' in prompt
    assert '효과나 시급성이 입증된 순위로 단정하지' in prompt


def test_facts_preserve_low_access_and_rank_direction_without_area_stereotypes(tmp_path):
    units = tmp_path / 'units.parquet'
    gap = tmp_path / 'gap.parquet'
    access = tmp_path / 'access.parquet'
    pd.DataFrame({'adm_cd': ['a', 'b'], 'adm_nm': ['가면', '나동'],
                  'pop_total': [1000, 1000], 'pop_foreign': [100, 20]}).to_parquet(units)
    pd.DataFrame({'adm_cd': ['a', 'b'], 'fac_type': ['보건의료'] * 2, 'rank': [1, 2]}).to_parquet(gap)
    # 다른 도메인이 같은 행정동에 있어도 의료 사실은 중복되거나 오염되지 않는다.
    pd.DataFrame({'adm_cd': ['a', 'b', 'a'], 'fac_type': ['보건의료', '보건의료', '금융'],
                  'access_index': [0.001, 0.004, 100]}).to_parquet(access)
    facts = policy.load_dong_facts(str(units), str(gap), str(tmp_path/'missing'), str(access))
    assert facts['가면']['access_norm'] == 0
    assert facts['가면']['access_rank'] == 2
    block = policy.build_dong_fact_block('가면', facts['가면'])
    assert '2개 지역' in block and '0.001000' in block
    assert '교통' not in block and '농어촌' not in block
    pd.DataFrame({'adm_cd': ['a'], 'fac_type': ['보건의료'], 'access_index': [0.001]}).to_parquet(access)
    with pytest.raises(ValueError, match='일치'):
        policy.load_dong_facts(str(units), str(gap), str(tmp_path/'missing'), str(access))


@pytest.mark.parametrize('mutate', [
    lambda p: p['policy_options'][0].pop('condition'),
    lambda p: p['policy_options'][0].pop('reason'),
    lambda p: p['policy_options'][1].update(title=''),
    lambda p: p['policy_options'].pop(),
    lambda p: p['policy_options'][1].update(reason='지원 효과가 71.5%입니다.'),
    lambda p: p['policy_options'][0].update(condition='  '),
    lambda p: p.update(checks=[]),
    lambda p: p.update(interpretation='외국인 비율은 71.5%입니다.'),
    lambda p: p['policy_options'][0].update(action='처방전 없이 약을 제공합니다.'),
    lambda p: p.update(interpretation='We need to propose a policy.'),
    lambda p: p.update(interpretation='[확인된 사실] 확인하지 않은 내용입니다.'),
    lambda p: p.update(interpretation='순위 안정성은 현장에서 확인이 필요합니다.'),
    lambda p: p['policy_options'][0].update(condition='이동 경로를 조사합니다.'),
])
def test_invalid_or_unguarded_policy_is_rejected(mutate):
    p = payload()
    mutate(p)
    with pytest.raises(ValueError):
        policy.parse_policy_response(json.dumps(p, ensure_ascii=False))


def test_renderer_keeps_facts_outside_model_control_and_preserves_evidence():
    from src.viz.dashboard import _policy_sections, _split_evidence_line
    parsed = policy.parse_policy_response(json.dumps(payload(), ensure_ascii=False))
    text = policy.render_policy_card('구룡포읍', 1, 0.882682, parsed, None)
    assert '[확인된 사실] 구룡포읍의 격차 점수는 0.883, 격차 순위는 1위' in text
    assert '확인 조건:' in text and '제안 이유:' in text
    assert '[우선 대응] 진료 이용 연결 지원' in text
    assert '[보완할 지원] 다국어 이용 안내 보완' in text
    assert '확정할 수 없습니다' in text
    body, evidence = _split_evidence_line(text)
    assert evidence.startswith('[근거]')
    sections = _policy_sections(body)
    assert len(sections) == 5
    assert sections[2]['label'] == '우선 대응'
    assert sections[3]['label'] == '보완할 지원'
    legacy = '[확인된 사실] 사실\n[해석] 해석\n[정책 후보] 후보\n[추가 확인] 확인'
    assert len(_policy_sections(legacy)) == 4
    assert '실제 의료기관 이동시간' in sections[-1]['text']
    assert _policy_sections('이전 형식 본문입니다.') == []
    stable = policy.render_policy_card('구룡포읍', 1, 0.883, parsed, {
        'pop_foreign': 1147, 'foreign_ratio': 0.151, 'foreign_ratio_rank': 1,
        'rank_min': 1, 'rank_max': 1,
    })
    assert '모든 임계거리에서 격차 상위 5위 이내' in stable


def fake_client(replies):
    items = iter(replies)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        result = next(items)
        if isinstance(result, Exception):
            raise result
        content, finish = result
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason=finish)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.with_options = lambda **_: client
    return client, calls


def test_generation_retries_truncated_json_and_saves_actual_model(monkeypatch):
    client, calls = fake_client([('{', 'length'), (json.dumps(payload(), ensure_ascii=False), 'stop')])
    monkeypatch.setattr(policy, '_client', lambda _: client)
    monkeypatch.setattr(policy.time, 'sleep', lambda _: None)
    card = policy.generate_policy_card('구룡포읍', 1, 0.883)
    assert len(calls) == 2
    assert '검증 실패' in calls[1]['messages'][-1]['content']
    assert card['card_version'] == 'action-plan-v2'
    assert card['model'] == calls[-1]['model']
    assert json.loads(card['policy_json']) == payload()
    assert '[추가 확인]' in card['policy_text']


def test_unavailable_model_fails_without_five_repeated_calls(monkeypatch):
    response = httpx.Response(404, request=httpx.Request('POST', 'https://example.test'))
    client, calls = fake_client([NotFoundError('not found', response=response, body=None)])
    monkeypatch.setattr(policy, '_client', lambda _: client)
    with pytest.raises(RuntimeError, match='HTTP 404'):
        policy.generate_policy_card('구룡포읍', 1, 0.883)
    assert len(calls) == 1


@pytest.mark.parametrize('text', [
    '외국인 비율이 상대적으로 높으면서 의료 접근성이 낮은 격차 유형입니다.',
    '외국인 주민 비율은 비교적 높고 의료 접근성은 낮습니다.',
    '이 지역은 수요 집중형으로 기존 서비스 이용을 점검합니다.',
    '외국인 비율은 낮고 공간 접근성은 양호합니다.',
])
def test_interpretation_cannot_reverse_supplied_gap_type(text):
    p = payload()
    p['interpretation'] = text
    with pytest.raises(ValueError):
        policy.validate_policy_facts(p, {'gap_type': '접근성 부족형(수요 낮음·접근성 낮음)'})


def test_matching_interpretation_accepts_relative_low_demand_and_low_access():
    p = payload()
    p['interpretation'] = '외국인 비율은 상대적으로 낮지만 의료 접근성이 낮아 이용 경로 점검이 필요합니다.'
    policy.validate_policy_facts(p, {'gap_type': '접근성 부족형(수요 낮음·접근성 낮음)'})


def test_policy_reason_cannot_reverse_access_even_when_interpretation_is_correct():
    p = payload()
    p['policy_options'][0]['reason'] = '의료 접근성이 높아 기존 안내를 보완합니다.'
    with pytest.raises(ValueError, match='제안 이유'):
        policy.validate_policy_facts(p, {'gap_type': '복합 취약형(수요 높음·접근성 낮음)'})


def test_generation_retries_when_model_reverses_demand_direction(monkeypatch):
    bad = payload()
    good = payload()
    good['interpretation'] = '외국인 비율이 낮지만 공간 접근성이 낮아 이용 경로 점검이 필요합니다.'
    client, calls = fake_client([(json.dumps(p, ensure_ascii=False), 'stop') for p in [bad, good]])
    monkeypatch.setattr(policy, '_client', lambda _: client)
    monkeypatch.setattr(policy.time, 'sleep', lambda _: None)
    facts = {'gap_type': '접근성 부족형(수요 낮음·접근성 낮음)', 'pop_foreign': 100,
             'foreign_ratio': 0.01, 'foreign_ratio_rank': 20, 'rank_min': 10, 'rank_max': 20}
    result = policy.generate_policy_card('장량동', 13, 0.4, facts=facts)
    assert len(calls) == 2
    assert '반대' in calls[1]['messages'][-1]['content']
    assert json.loads(result['policy_json'])['interpretation'] == good['interpretation']
