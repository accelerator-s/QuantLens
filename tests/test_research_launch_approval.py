"""Generating a checked plan never launches an experiment without a click."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from exceptions import ContractViolation
from webui.backend.errors import install_error_handlers
from webui.backend.routes import factor_chat, strategy_ideas
from webui.backend.tasks import TaskManager


def test_factor_candidate_generation_stops_before_evaluation(monkeypatch):
    candidate = dict(model_name='chosen-model', factor_name='checked_factor', display_name='待验证因子', assumption='检验收益延迟反应',
                     direction='positive', timescale='daily', dependencies=['close'], code='def compute(panel): return panel["close"]')
    ai = SimpleNamespace(pick=lambda *args, **kwargs: SimpleNamespace(name='chosen-model'),
                         run_task=Mock(return_value=candidate))
    monkeypatch.setattr(factor_chat, 'AIRouter', lambda: ai)
    monkeypatch.setattr(factor_chat, '_candidate_prompt', lambda *args: '研究讨论')
    monkeypatch.setattr(factor_chat, '_validate_candidate', Mock())
    monkeypatch.setattr(factor_chat, '_check_candidate', Mock())
    monkeypatch.setattr(factor_chat, '_register_draft', Mock(return_value=('proposal', 'research_checked_factor')))
    launch = Mock()
    monkeypatch.setattr(factor_chat, 'start_factor_evolution', launch)
    pending = {}
    def start(name, fn, **kwargs):
        pending['run'] = fn
        return 'generation-task'
    monkeypatch.setattr(factor_chat, 'manager', SimpleNamespace(start=start))
    factor_chat.start_factor_chat_research(factor_chat.FactorChatPayload(messages=[{'role': 'user', 'content': '检验反应延迟'}]))
    result = pending['run'](SimpleNamespaceReport())
    launch.assert_not_called()
    factor_chat._check_candidate.assert_called_once()
    factor_chat._register_draft.assert_called_once()
    assert result['approval_status'] == 'pending'
    assert result['model'] == 'chosen-model'
    assert 'research_id' not in result


class SimpleNamespaceReport:
    def __call__(self, line):
        pass
    def progress(self, payload):
        pass


@pytest.fixture(params=['factor', 'strategy'])
def approval(request, tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    manager = TaskManager()
    factor = request.param == 'factor'
    module = factor_chat if factor else strategy_ideas
    resource = 'factor-ai-research' if factor else 'strategy-ai-research'
    launch = Mock(return_value={'research_id': 'research-1', 'task_id': 'experiment-1', 'program_id': 'program-1'})
    monkeypatch.setattr(module, 'manager', manager)
    monkeypatch.setattr(module, 'start_factor_evolution' if factor else 'start_research', launch)
    result = {'approval_status': 'pending', 'factor_name': 'checked_factor', 'profile_name': 'checked_strategy', 'model': 'chosen-model'}
    manager._tasks['generation-task'] = {
        'task_id': 'generation-task', 'name': 'generation', 'created_at': '2026-11-14T00:00:00Z',
        'status': 'completed', 'resource': resource, 'progress': {'step': '等待启动实验'},
        'result': result, 'log': [], 'error': '',
    }
    manager._persist(manager._tasks['generation-task'])
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(module.router, prefix='/api')
    path = '/api/factors/chat/research' if factor else '/api/strategies/research'
    with TestClient(app) as client:
        yield SimpleNamespace(manager=manager, module=module, launch=launch, client=client,
                              path=path + '/generation-task', factor=factor)


def test_refresh_keeps_plan_pending_and_click_starts_only_once(approval, monkeypatch):
    restored = TaskManager()
    monkeypatch.setattr(approval.module, 'manager', restored)
    pending = approval.client.get(approval.path + '/progress').json()
    assert pending['result']['approval_status'] == 'pending'
    approval.launch.assert_not_called()
    response = approval.client.post(approval.path + '/approve', json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['approval_status'] == 'approved'
    assert result['approved_at']
    assert result['research_id' if approval.factor else 'experiment_id'] == ('research-1' if approval.factor else 'experiment-1')
    if approval.factor:
        approval.launch.assert_called_once_with('checked_factor', model_name='chosen-model')
    else:
        approval.launch.assert_called_once_with('checked_strategy')
    # A retry after the server restarts must return the same launch.
    monkeypatch.setattr(approval.module, 'manager', TaskManager())
    assert approval.client.post(approval.path + '/approve', json={}).json() == result
    assert approval.launch.call_count == 1


def test_unfinished_or_wrong_workflow_cannot_be_approved(approval):
    record = approval.manager._tasks['generation-task']
    for status in ['running', 'failed', 'interrupted']:
        record['status'] = status
        assert approval.client.post(approval.path + '/approve', json={}).status_code == 422
    record['status'] = 'completed'
    record['resource'] = 'other-workflow'
    assert approval.client.post(approval.path + '/approve', json={}).status_code == 404
    approval.launch.assert_not_called()


def test_failed_launch_remains_pending_and_can_be_retried(approval):
    approval.launch.side_effect = ContractViolation(code='LAUNCH_FAILED', message='启动失败')
    assert approval.client.post(approval.path + '/approve', json={}).status_code == 422
    assert approval.manager.get('generation-task')['result']['approval_status'] == 'pending'
    approval.launch.side_effect = None
    assert approval.client.post(approval.path + '/approve', json={}).status_code == 200
    assert approval.launch.call_count == 2


def test_concurrent_clicks_start_only_one_experiment(approval):
    approve = approval.module.approve_factor_chat_research if approval.factor else approval.module.approve_idea_research
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, ['generation-task', 'generation-task']))
    assert results[0] == results[1]
    approval.launch.assert_called_once()
