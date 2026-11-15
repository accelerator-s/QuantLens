from unittest.mock import Mock

import pytest

from exceptions import QuantTrainingError
from factor_evolution.engine import EvolutionEngine
from factor_evolution.models import EvolutionConfig, EvolutionState, FactorVersion, ReviewResult, WindowResult
from factor_evolution.store import EvolutionStore
from paths import Workspace


def test_search_never_sees_validation_and_freezes_before_evaluation(tmp_path):
    config = EvolutionConfig(base_factor='probe', profile_name='probe',
        train_window={'start':'2018-01-01','end':'2021-12-31'},
        validation_window={'start':'2022-01-01','end':'2024-12-31'},
        test_window={'start':'2025-01-01','end':'2026-01-01'},
        targets={'min_annualized_excess_return':.03,'max_drawdown':.2,
                 'max_annualized_turnover':6,'min_oos_stability':.5}, max_iterations=2)
    base = FactorVersion(version_id='v0', factor_name='probe', assumption='probe', direction='positive',
        timescale='daily', dependencies=['close'], code='def compute(p): return p["close"]',
        change_type='baseline', change_summary='baseline')
    store = EvolutionStore(Workspace(tmp_path))
    calls = []

    class Services:
        record_result = Mock()
        record_review = Mock()
        def summarize_experiment(self, cfg, evidence):
            assert evidence['validation_completed'] and not evidence['locked_test_completed']
            assert evidence['state'] == 'completed'
            assert not any(key in evidence for key in ['metrics', 'validation', 'test'])
            assert store.load_status(rid).finished_at
            return {'conclusion': '候选未通过验证门槛，未运行锁定测试。',
                    'ai_generated': True, 'model_name': 'configured-model'}
        def load_base(self, _): return base
        def check_candidate(self, *args, **kwargs): pass
        def begin_experiment(self, cfg, version, parent): return 'EXP-' + version.version_id
        def propose(self, cfg, parent, memory, *, iteration, **kwargs):
            assert not any(name.endswith('validation') for _, name in calls)
            assert all(not item.get('metrics', {}).get('validation') for item in memory)
            return base.model_copy(update={'version_id':f'v{iteration}', 'parent_version_id':parent.version_id,
                'code':f'def compute(p): return p["close"].rolling({iteration+2}).mean()'})
        def review(self, cfg, version, result, best_result, **kwargs):
            assert result.validation is best_result.validation is None
            assert result.test is best_result.test is None
            return ReviewResult(decision='continue', assessment='training evidence only')
        def evaluate(self, cfg, version, *, window_name, **kwargs):
            if window_name != 'fixed_train':
                frozen = [e for e in store.detail(rid)['events'] if e['event_type'] == 'finalist_frozen']
                assert len(frozen) == 1 and frozen[0]['payload']['version_id'] == 'v1'
            calls.append((version.version_id, window_name))
            net = {'v0':.1,'v1':.12,'v2':.11}[version.version_id]
            if window_name.endswith('validation') and version.version_id == 'v1': net = -.1
            return WindowResult(window=window_name, run_id=f'BT-{len(calls)}', metrics={
                'research_standard':'cn_a_daily_v3', 'input_quality':{'passed':True},
                'returns':{'annualized_return':net,'annualized_excess_return':net},
                'risk':{'max_drawdown':-.1}, 'objective':{'value':net/.1,'eligible':True},
                'trading':{'annualized_turnover_oneside':2}})

    engine = EvolutionEngine(store, Services()); rid = engine.create(config)
    outcome = engine.run(rid)
    assert [name for _,name in calls[:3]] == ['fixed_train'] * 3
    assert len(calls) == 7 and not any(name == 'test' for _,name in calls)
    assert outcome['status']['decision'] == 'stopped'
    assert '未通过验证' in outcome['status']['terminal_reason']
    assert outcome['events'][-2]['event_type'] == 'experiment_summary'
    assert not any('experiment_summary' in memory for memory in store.memories(rid))
    status = store.load_status(rid); status.state = EvolutionState.FAILED; store.save_status(status)
    with pytest.raises(QuantTrainingError) as error:
        engine.run(rid)
    assert error.value.code == 'EVOLUTION_VALIDATION_TERMINAL'
    assert len(calls) == 7


def test_invalid_candidate_does_not_abort_remaining_training_budget(tmp_path):
    from factor_evolution.supervision import check_source
    config = EvolutionConfig(base_factor='probe', profile_name='probe',
        train_window={'start':'2018-01-01','end':'2021-12-31'},
        validation_window={'start':'2022-01-01','end':'2024-12-31'},
        test_window={'start':'2025-01-01','end':'2026-01-01'},
        targets={'min_annualized_excess_return':.03,'max_drawdown':.2,
                 'max_annualized_turnover':6,'min_oos_stability':.5}, max_iterations=2)
    base = FactorVersion(version_id='v0', factor_name='probe', assumption='probe', direction='positive',
        timescale='daily', dependencies=['close'], code='def compute(p): return p["close"]',
        change_type='baseline', change_summary='baseline')
    service = Mock()
    service.load_base.return_value = base
    service.check_candidate.side_effect = lambda cfg, v, **kwargs: check_source(v.code)
    service.begin_experiment.side_effect = lambda cfg, v, parent: 'EXP-'+v.version_id
    service.propose.side_effect = [
        base.model_copy(update={'version_id':'v1','code':'def compute(p): return (p["close"]'}),
        base.model_copy(update={'version_id':'v2','code':'def compute(p): return p["close"]*2'})]
    service.evaluate.return_value = WindowResult(window='fixed_train', run_id='BT-probe', metrics={
        'research_standard':'cn_a_daily_v3','input_quality':{'passed':True},
        'returns':{'annualized_return':.1,'annualized_excess_return':.1},
        'risk':{'max_drawdown':-.1},'objective':{'value':1.,'eligible':True},
        'trading':{'annualized_turnover_oneside':2}})
    service.review.return_value = ReviewResult(decision='continue', assessment='training only')
    service.summarize_experiment.return_value = {'conclusion':'no improvement','ai_generated':False}
    store = EvolutionStore(Workspace(tmp_path))
    engine = EvolutionEngine(store, service)
    result = engine.run(engine.create(config))
    assert result['status']['state'] == 'completed'
    assert result['status']['failed_rounds'] == 1
    assert service.propose.call_count == 2
    assert service.evaluate.call_count == 2  # Baseline and valid candidate only.
    assert all(c.kwargs['window_name'] == 'fixed_train' for c in service.evaluate.call_args_list)
    failures = [e['payload'] for e in result['events'] if e['event_type']=='round_failed']
    assert failures[0]['code'] == 'EVOLUTION_SOURCE_INVALID'
