import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from exceptions import ContractViolation
from lifecycle import model_reviews as reviews


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    return tmp_path


def model(payload=None):
    router = Mock()
    router.pick.return_value = SimpleNamespace(name='configured-model')
    router.run_task.return_value = payload or {
        'config_kind': 'model_review', 'proposed_value': {
            'conclusion': '当前回测不足以支持盈利假设。', 'risks': ['费用侵蚀收益'],
            'next_steps': ['核对不同年份的表现']}}
    return router


@pytest.mark.parametrize('kind', ['factor', 'strategy'])
def test_generate_persists_actual_model_and_evidence_without_changing_gates(isolated, monkeypatch, kind):
    evidence = {'scope': '最终评价', 'run_id': 'BT-1', 'version': 'v2', 'accepted': False,
                'metrics': {'annualized_return': -.08}}
    router = model()
    record = reviews.generate_review(kind, 'alpha', evidence=evidence,
                                     model_name='configured-model', router=router)
    assert evidence['accepted'] is False
    router.pick.assert_called_once_with('structured_config', model_name='configured-model')
    request = router.run_task.call_args.kwargs
    assert request['model_name'] == 'configured-model'
    assert json.loads(request['prompt'])['evidence'] == evidence
    assert record['model_name'] == 'configured-model'
    assert record['evidence'] == evidence and record['evidence_hash']
    path = isolated / 'runs' / 'model_reviews' / kind / 'alpha' / (record['review_id'] + '.json')
    assert json.loads(path.read_text()) == record
    monkeypatch.setattr(reviews, '_research_reviews', lambda *args: iter([]))
    assert reviews.list_reviews(kind, 'alpha') == [record]
    assert reviews.list_reviews(kind, 'other') == []


@pytest.mark.parametrize('payload', [
    {'config_kind': 'wrong', 'proposed_value': {'conclusion': '假的结论'}},
    {'config_kind': 'model_review', 'proposed_value': {'conclusion': '   '}},
    {'config_kind': 'model_review', 'proposed_value': {'conclusion': '结论', 'accepted': True}},
])
def test_invalid_model_output_is_not_replaced_by_template(isolated, payload):
    with pytest.raises((ContractViolation, ValidationError)):
        reviews.generate_review('factor', 'alpha', evidence={'scope': '已发布', 'run_id': 'E1'},
                                model_name='configured-model', router=model(payload))
    assert not list(isolated.rglob('REV-*.json'))


def test_factor_history_uses_real_model_review_and_original_candidate_scope(isolated, monkeypatch):
    import factor_evolution.store
    store = Mock()
    store.list.return_value = [{'research_id': 'EVO-1', 'base_factor': 'alpha'},
                               {'research_id': 'EVO-2', 'base_factor': 'other'}]
    review = {'assessment': '候选的训练期表现仍弱', 'model_name': 'research-model',
              'ai_generated': True, 'failure_reason': '样本不足', 'next_change': '检查数据'}
    store.detail.return_value = {'events': [
        {'created_at': '2026-11-15T10:00:00Z', 'event_type': 'round_completed',
         'payload': {'version_id': 'candidate-v3', 'review': review}},
        {'created_at': '2026-11-15T10:01:00Z', 'event_type': 'round_completed',
         'payload': {'review': {**review, 'ai_generated': False}}}]}
    monkeypatch.setattr(factor_evolution.store, 'EvolutionStore', lambda: store)
    records = reviews.list_reviews('factor', 'alpha')
    assert len(records) == 1
    assert records[0]['scope'] == '训练期候选复盘'
    assert records[0]['model_name'] == 'research-model'
    assert records[0]['version'] == 'candidate-v3'
    store.detail.assert_called_once_with('EVO-1')


def test_strategy_history_does_not_attribute_other_studies_to_this_strategy(isolated, monkeypatch):
    import strategy.research
    current_path = isolated / 'result.json'
    result = {'run_id': 'BT-current', 'search': {'dialogues': [[
        {'action': 'query', 'section': 'costs'}, {'action': 'stop', 'stop_reason': '训练期没有稳定改善'}]]}}
    current_path.write_text(json.dumps(result))
    root = isolated / 'studies' / 'other'; root.mkdir(parents=True)
    (root / 'result.json').write_text(json.dumps({**result, 'profile_name': 'other', 'run_id': 'BT-other'}))
    monkeypatch.setattr(strategy.research, 'read_result', lambda name: result)
    monkeypatch.setattr(strategy.research, 'result_path', lambda name: current_path)
    records = reviews.list_reviews('strategy', 'alpha')
    assert len(records) == 1
    assert records[0]['source_id'] == 'BT-current'
    assert records[0]['scope'] == '训练期选模诊断'
    assert records[0]['model_name'] == ''  # Historical data does not record the model.


def test_factor_evidence_is_current_version_and_latest_published_window(isolated, monkeypatch):
    import factors.registry
    from quantlens import ads, evaluation_view
    monkeypatch.setattr(factors.registry, 'FactorRegistry', lambda: SimpleNamespace(get=lambda _: SimpleNamespace(
        version='v2', code_hash='hash2', assumption='假设', direction='negative')))
    row = {'run_id': 'new', 'eval_start': '2024-01-01', 'eval_end': '2024-12-31', 'horizon': 5}
    calls = []
    def query(table, where, args, **kwargs):
        calls.append((table, where, args))
        if table == 'factor_eval':
            return [row, {**row, 'run_id': 'old'}, {**row, 'eval_start': '2023-01-01'}]
        return [{'date': '2024-01-05'}, {'date': '2023-12-31'}]
    monkeypatch.setattr(ads, 'query', query)
    monkeypatch.setattr(evaluation_view, 'view', lambda rows, daily, *args: {'rows': rows, 'daily': daily})
    monkeypatch.setattr(reviews, '_factor_sessions', lambda _: (None, [{'model_name': 'research-model'}]))
    evidence, preferred = reviews.review_evidence('factor', 'alpha')
    assert calls[0][2] == ['alpha', 'v2']
    assert evidence['metrics']['rows'] == [row]
    assert evidence['metrics']['daily'] == [{'date': '2024-01-05'}]
    assert evidence['version'] == 'v2' and preferred == 'research-model'
    assert '未扣交易费用' in evidence['limitations']


def test_strategy_evidence_requires_completed_result(isolated, monkeypatch):
    import strategy.research
    monkeypatch.setattr(strategy.research, 'require_strategy', lambda _: SimpleNamespace(extra={}))
    monkeypatch.setattr(strategy.research, 'read_result', lambda _: {'state': 'computed'})
    with pytest.raises(ContractViolation, match='完成策略回测'):
        reviews.review_evidence('strategy', 'alpha')


def test_backend_routes_offer_read_and_background_generation(isolated, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webui.backend.routes import model_reviews as route
    from webui.backend.tasks import TaskManager
    import time
    manager = TaskManager()
    monkeypatch.setattr(route, 'manager', manager)
    monkeypatch.setattr(route, '_require', lambda *args: None)
    monkeypatch.setattr(route, 'review_evidence', lambda *args: ({'run_id': 'BT-1'}, 'configured-model'))
    monkeypatch.setattr(route, 'list_reviews', lambda *args: [])
    generate = Mock(return_value={'review_id': 'REV-1'})
    monkeypatch.setattr(route, 'generate_review', generate)
    app = FastAPI(); app.include_router(route.router, prefix='/api')
    with TestClient(app) as client:
        for kind in ['factors', 'strategies']:
            path = f'/api/{kind}/alpha/reviews'
            assert client.get(path).json()['items'] == []
            assert not generate.called
            response = client.post(path, json={})
            assert response.status_code == 200
            task_id = response.json()['task_id']
            for _ in range(100):
                task = manager.get(task_id)
                if task['status'] != 'running': break
                time.sleep(.01)
            assert task['status'] == 'completed'
            assert client.get(path).json()['task']['status'] == 'completed'
            generate.assert_called_once()
            assert generate.call_args.kwargs['model_name'] == 'configured-model'
            generate.reset_mock()
