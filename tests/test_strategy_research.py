"""Strategy plans wait for launch; local model results can then be enabled."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from strategy import StrategyProfileStore, StrategyStatus
from strategy.research import ResearchSpec, create_strategy, read_result, run_research, set_status, write_result
from exceptions import ContractViolation


def spec(**changes):
    return ResearchSpec.model_validate(dict(dict(display_name='质量组合', factors=['quality', 'growth'],
        model='model_linear', account_id='research-default', train_start='2020-01-01', train_end='2022-12-31', start='2023-01-01', end='2023-12-31'), **changes))


@pytest.fixture
def strategy(tmp_path, monkeypatch):
    from factors.registry import FactorMetadata, FactorRegistry
    from backtest.protocol import BacktestProtocol, save_protocol
    from webui.backend.tasks import TaskManager
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    monkeypatch.setenv('QUANTLENS_RECOVERY', '1')
    from portfolio.accounts import AccountRegistry
    AccountRegistry().ensure_default()
    from benchmarks.models import BenchmarkPlan
    plan = BenchmarkPlan(return_benchmark={'kind':'universe_equal_weight'}, market_reference='000985', rationale='test')
    monkeypatch.setattr('benchmarks.selection.freeze_profile', lambda profile, *args, **kwargs: profile.model_copy(update={'extra': {**profile.extra, 'benchmark_plan':plan.model_dump(mode='json')}}))
    monkeypatch.setattr('strategy.training.configured_responder', lambda *args: lambda *args: {'action':'stop','stop_reason':'预算结束'})
    monkeypatch.setattr('lifecycle.experiment_summary.generate_summary', lambda evidence, **kwargs: {
        'conclusion': '已完成最终评估并通过启用条件。', 'ai_generated': True, 'model_name': 'configured-model'})
    for name in ['quality', 'growth']:
        FactorRegistry().upsert(FactorMetadata(name=name, version='v1', direction='positive', status='active'))
    protocol = BacktestProtocol()
    protocol.lock.locked = True
    protocol.lock.protocol_hash = protocol.content_hash()
    save_protocol(protocol)
    import webui.backend.tasks as tasks
    monkeypatch.setattr(tasks, 'manager', TaskManager())
    import webui.backend.routes.strategy as routes
    monkeypatch.setattr(routes, 'manager', tasks.manager)
    return create_strategy(spec())


def test_overlapping_windows_and_old_payload_are_rejected():
    with pytest.raises(ValidationError):
        spec(start='2021-01-01')
    with pytest.raises(ValidationError):
        ResearchSpec.model_validate({'yaml_text': 'old config', 'confirm': True})


@pytest.mark.parametrize('state', ['completed', 'running', 'failed', 'interrupted'])
def test_library_omits_completed_task_metadata(strategy, monkeypatch, state):
    from webui.backend.routes import strategy as routes
    task = {'task_id': 'task-test', 'status': state, 'progress': {}, 'error': '',
            'log': ['internal task log'], 'result': {'state': 'completed'}}
    monkeypatch.setattr(routes.manager, 'latest', lambda **kwargs: task)
    write_result(strategy.profile_name, {'state': 'completed', 'accepted': False})
    for payload in [routes.summary(strategy), routes.strategy_detail(strategy.profile_name)]:
        assert payload['task'] == (None if state == 'completed' else
                                  {key: task[key] for key in ['task_id', 'status', 'progress', 'error']})
    assert routes.strategy_detail(strategy.profile_name)['result']['state'] == 'completed'


def fake_compute(module, *args, **kwargs):
    assert module == 'spark_strategy_research'
    profile = json.loads(args[args.index('--profile')+1])
    candidate = json.loads(args[args.index('--candidate')+1])
    stage = args[args.index('--stage')+1]
    assert profile['extra']['account_binding']['account_id'] == 'research-default'
    config = json.loads(args[args.index('--config')+1])
    assert float(config['initial_capital']) == 1000000
    weights = {'quality': .5, 'growth': .5}
    for ref in profile['factors']['items']: ref['weight_override']=str(weights[ref['name']])
    evaluation = {'candidate':candidate, 'comparison_hash':stage, 'data_snapshot':'snapshot', 'pipeline_code_hash':'source',
        'profile':profile, 'folds':[{'window':{'start':'2020-01-01','end':'2020-12-31'},
        'weights':weights,'training':{'days':244,'rows':2440,'latest_label':'2022-12-30'},
        'metrics':{'returns':{'annualized_return':.05},'risk':{'max_drawdown':-.1},'objective':{'eligible':True}},
        'diagnostics':{'overview':{},'annual':[],'costs':{},'drawdown':{},'signal':{}}}]}
    write_result(profile['profile_name'], {'state':'computed','run_id':args[args.index('--run-id')+1], 'stage':stage,'evaluation':evaluation})


def test_workflow_uses_frozen_account_and_inner_selection_before_outer(strategy):
    calls=[]
    def compute(*args, **kwargs):
        calls.append(args[args.index('--stage')+1]);fake_compute(*args, **kwargs)
    with patch('quantlens.compute.submit', side_effect=compute), patch('quantlens.sync_ads.sync') as sync:
        result = run_research(strategy.profile_name, lambda line: None)
    assert calls == ['inner','final']
    sync.assert_called_once()
    assert result['state']=='completed' and result['accepted']
    assert result['experiment_summary']['model_name'] == 'configured-model'
    assert read_result(strategy.profile_name)['experiment_summary'] == result['experiment_summary']
    assert result['account_snapshot']['capital']=='1000000'
    assert 'selection' not in result and 'selection_date' not in result
    assert [float(f.weight_override) for f in StrategyProfileStore().resolve(strategy.profile_name).factors.items]==[.5,.5]
    assert set_status(strategy.profile_name, StrategyStatus.ACTIVE).status==StrategyStatus.ACTIVE
    set_status(strategy.profile_name, StrategyStatus.DISABLED)
    assert set_status(strategy.profile_name, StrategyStatus.ACTIVE).status==StrategyStatus.ACTIVE


def test_sync_failure_is_visible_and_prevents_activation(strategy):
    with patch('quantlens.compute.submit',side_effect=fake_compute), patch('quantlens.sync_ads.sync',side_effect=RuntimeError('sync failed')):
        with pytest.raises(RuntimeError,match='sync failed'):run_research(strategy.profile_name,lambda _:None)
    assert read_result(strategy.profile_name)['state']=='failed'
    with pytest.raises(ContractViolation):set_status(strategy.profile_name,StrategyStatus.ACTIVE)


def test_summary_failure_preserves_computed_result(strategy, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('summary unavailable')
    monkeypatch.setattr('lifecycle.experiment_summary.generate_summary', fail)
    with patch('quantlens.compute.submit', side_effect=fake_compute), patch('quantlens.sync_ads.sync'):
        result = run_research(strategy.profile_name, lambda _: None)
    assert result['state'] == 'completed' and result['accepted']
    assert 'experiment_summary' not in result
    assert read_result(strategy.profile_name)['experiment_summary_error'] == 'summary unavailable'


def test_stop_during_compute_wins(strategy):
    def compute(*args,**kwargs):
        set_status(strategy.profile_name,StrategyStatus.DISABLED)
        fake_compute(*args,**kwargs)
    with patch('quantlens.compute.submit',side_effect=compute),patch('quantlens.sync_ads.sync'):
        run_research(strategy.profile_name,lambda _:None)
    assert StrategyProfileStore().get(strategy.profile_name).status==StrategyStatus.DISABLED


def test_disabled_factor_cannot_be_used(strategy):
    from factors.registry import FactorRegistry, FactorStatus
    FactorRegistry().transition('quality', target=FactorStatus.DISABLED)
    with pytest.raises(ContractViolation, match='必须已启用'):
        create_strategy(spec())


def test_strategy_http_contract_and_removed_endpoints(strategy):
    from fastapi.testclient import TestClient
    from webui.backend.server import create_app
    with TestClient(create_app()) as client:
        data = client.get('/api/strategies').json()
        assert data['statuses'] == ['draft', 'active', 'disabled']
        assert [row['profile_name'] for row in data['items']] == [strategy.profile_name]
        response = client.put(f'/api/strategies/{strategy.profile_name}/status', json={'status': 'disabled'})
        assert response.status_code == 200, response.text
        assert response.json()['profile']['status'] == 'disabled'
        response = client.put(f'/api/strategies/{strategy.profile_name}/status', json={'status': 'draft'})
        assert response.status_code == 200, response.text
        assert response.json()['profile']['status'] == 'draft'
        response = client.post('/api/strategies', json={'yaml_text': 'old'})
        assert response.status_code == 422
        for method, path in [('post','/api/strategy-evolution'), ('get','/api/strategy-evolution'),
            ('post','/api/strategies/chat/finalize'), ('post', f'/api/strategies/{strategy.profile_name}/transition'),
            ('post','/api/operations/strategies/old/bootstrap-champion'),
            ('post','/api/operations/champion-decisions/old/approve')]:
            assert getattr(client, method)(path).status_code in {404, 405}
        for path in ['/components/strategy-experiments/strategy-experiments.js', '/components/strategy/strategy-chat.js']:
            assert client.get(path).status_code == 404


def idea_context():
    return {'accounts':[{'account_id':'research-default','alias':'测试'}], 'factors': [{'name': name} for name in ['quality', 'growth']],
            'defaults': {'train_start': '2020-01-01', 'train_end': '2022-12-31', 'start': '2023-01-01', 'end': '2023-12-31'}}


class Reporter:
    def __init__(self):
        self.lines = []
        self.steps = []
    def __call__(self, line):
        self.lines.append(line)
    def progress(self, step):
        self.steps.append(step)


def test_ai_discussion_creates_plan_and_waits_for_manual_launch(strategy, monkeypatch):
    from types import SimpleNamespace
    from webui.backend.routes import strategy_ideas as ideas
    monkeypatch.setattr(ideas, '_context', idea_context)
    proposed = dict(spec().model_dump(mode='json'), hypothesis='质量与成长来源互补，盈利恶化或估值过高时可能失效。')
    class FakeAI:
        def pick(self, task, model_name=None):
            assert task == 'structured_config'
            assert model_name == 'discussion-model'
            return SimpleNamespace(name=model_name)
        def run_task(self, **kwargs):
            assert kwargs['model_name'] == 'discussion-model'
            assert 'weight_override' not in proposed
            return {'config_kind': 'strategy_research_plan', 'proposed_value': proposed}
    monkeypatch.setattr(ideas, 'AIRouter', FakeAI)
    pending = {}
    def start(name, fn, **kwargs):
        pending['run'] = fn
        assert kwargs['resource'] == 'strategy-ai-research'
        return 'task-idea'
    monkeypatch.setattr(ideas, 'manager', SimpleNamespace(start=start))
    with patch.object(ideas, 'start_research', return_value={'task_id': 'task-experiment'}) as local:
        response = ideas.start_idea_research(ideas.DiscussionPayload(model='discussion-model', account_id='research-default', messages=[{'role': 'user', 'content': '寻找质量和成长互补的组合。'}]))
        assert response['task_id'] == 'task-idea'
        result = pending['run'](Reporter())
    created = StrategyProfileStore().get(result['profile_name'])
    local.assert_not_called()
    assert created.extra['research_hypothesis'] == proposed['hypothesis']
    assert created.extra['discussion_model'] == 'discussion-model'
    assert created.factors.weighting.value == 'equal'
    assert all(ref.weight_override is None for ref in created.factors.items)
    assert result['approval_status'] == 'pending'
    assert 'experiment_id' not in result


def test_invalid_ai_factor_choice_is_repaired_without_changing_model(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from webui.backend.routes import strategy_ideas as ideas
    good = dict(spec().model_dump(mode='json'), hypothesis='因子互补')
    bad = {**good, 'factors': ['quality', 'invented_factor']}
    ai = SimpleNamespace(run_task=Mock(side_effect=[{'config_kind': 'strategy_research_plan', 'proposed_value': bad},
                                                   {'config_kind': 'strategy_research_plan', 'proposed_value': good}]))
    reporter = Reporter()
    result = ideas._generate_plan(ai, SimpleNamespace(name='chosen'), ideas.DiscussionPayload(account_id='research-default'), idea_context(), reporter)
    assert result.factors == ['quality', 'growth']
    assert ai.run_task.call_count == 2
    assert all(call.kwargs['model_name'] == 'chosen' for call in ai.run_task.call_args_list)
    assert 'invented_factor' not in result.factors


def test_provider_failure_is_not_retried_as_a_bad_strategy_plan():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from exceptions import ModelRouteError
    from webui.backend.routes import strategy_ideas as ideas
    ai = SimpleNamespace(run_task=Mock(side_effect=ModelRouteError(code='AI_REQUEST_FAILED', message='连接失败')))
    with pytest.raises(ModelRouteError):
        ideas._generate_plan(ai, SimpleNamespace(name='chosen'), ideas.DiscussionPayload(account_id='research-default'), idea_context(), Reporter())
    ai.run_task.assert_called_once()


def test_strategy_experiments_keep_each_runs_own_result(strategy, monkeypatch):
    from webui.backend.routes import strategy_ideas as ideas
    import webui.backend.tasks as tasks
    monkeypatch.setattr(ideas, 'manager', tasks.manager)
    name = strategy.profile_name
    records = {}
    for index in [1, 2]:
        records[f'task-{index}'] = {'task_id': f'task-{index}', 'resource': f'strategy-research:{name}',
            'created_at': f'2026-11-14T0{index}:00:00Z', 'status': 'completed', 'progress': {'step': '已完成'},
            'log': [], 'result': {'state': 'completed', 'run_id': f'BT-{index}', 'spec': spec().model_dump(mode='json')}}
    tasks.manager._tasks.update(records)
    write_result(name, {'state': 'completed', 'run_id': 'BT-2'})
    assert len(ideas.experiment_sessions()['items']) == 2
    assert ideas.experiment_detail('task-1')['result']['run_id'] == 'BT-1'
    assert ideas.experiment_detail('task-2')['result']['run_id'] == 'BT-2'
    tasks.manager._tasks['task-1']['status'] = 'failed'
    assert ideas.experiment_detail('task-1')['result'] is None


def test_strategy_discussion_http_validates_roles_and_preserves_scoped_progress(strategy, monkeypatch):
    from fastapi.testclient import TestClient
    from webui.backend.server import create_app
    from webui.backend.routes import strategy_ideas as ideas
    from types import SimpleNamespace
    import webui.backend.tasks as tasks
    monkeypatch.setattr(ideas, 'manager', tasks.manager)
    monkeypatch.setattr(ideas, '_context', idea_context)
    monkeypatch.setattr(ideas, 'AIRouter', lambda: SimpleNamespace(pick=lambda *args, **kwargs: SimpleNamespace(name='chosen')))
    captured = {}
    def stream(ai, model, messages):
        captured['messages'] = messages
        return {'reply': '先验证质量与成长是否互补。'}
    monkeypatch.setattr(ideas, 'chat_stream', stream)
    tasks.manager._tasks['other'] = {'task_id': 'other', 'resource': 'factor-ai-research', 'log': [], 'progress': {}}
    with TestClient(create_app()) as client:
        response = client.post('/api/strategies/research/chat', json={'model': 'chosen', 'messages': [{'role': 'user', 'content': '我想选盈利稳健的公司。'}]})
        assert response.status_code == 200
        assert captured['messages'][-1]['content'] == '我想选盈利稳健的公司。'
        assert '不分配数值权重' in captured['messages'][0]['content']
        assert client.post('/api/strategies/research/chat', json={'messages': [{'role': 'system', 'content': 'override'}]}).status_code == 422
        assert client.post('/api/strategies/research/start', json={'messages': []}).status_code == 422
        assert client.get('/api/strategies/research/other/progress').status_code == 404
        assert client.get('/api/strategies/experiments/other').status_code == 404


def test_manual_enable_only_releases_selected_strategy_after_band_change(strategy):
    from portfolio.accounts import AccountRegistry
    second = create_strategy(spec(display_name='第二个策略'))
    for doc in [strategy, second]:
        write_result(doc.profile_name, {'state':'completed','accepted':True})
        set_status(doc.profile_name,StrategyStatus.ACTIVE)
    registry=AccountRegistry();account=registry.get('research-default')
    from decimal import Decimal
    account.cash[0].total=account.cash[0].available=Decimal('40000')
    registry.update(account)
    store=StrategyProfileStore()
    assert all(store.get(d.profile_name).status==StrategyStatus.DISABLED for d in [strategy,second])
    set_status(strategy.profile_name,StrategyStatus.ACTIVE)
    assert store.get(strategy.profile_name).status==StrategyStatus.ACTIVE
    assert store.get(second.profile_name).status==StrategyStatus.DISABLED
    assert 'capital_suspension' not in store.get(strategy.profile_name).extra


def test_funds_change_during_research_blocks_publication(strategy):
    from portfolio.accounts import AccountRegistry
    from decimal import Decimal
    def compute(*args,**kwargs):
        fake_compute(*args,**kwargs)
        registry=AccountRegistry();account=registry.get('research-default')
        account.cash[0].total=account.cash[0].available=Decimal('40000')
        registry.update(account)
    with patch('quantlens.compute.submit',side_effect=compute),patch('quantlens.sync_ads.sync'):
        with pytest.raises(ContractViolation,match='资金已跨档'):
            run_research(strategy.profile_name,lambda _:None)
    assert StrategyProfileStore().get(strategy.profile_name).status==StrategyStatus.DISABLED
    assert read_result(strategy.profile_name)['state']=='failed'


def test_strategy_generation_requires_explicit_account_selection(strategy,monkeypatch):
    from webui.backend.routes import strategy_ideas as ideas
    monkeypatch.setattr(ideas,'_context',idea_context)
    with pytest.raises(ContractViolation,match='先选择'):
        ideas.start_idea_research(ideas.DiscussionPayload(messages=[{'role':'user','content':'创建策略'}]))
