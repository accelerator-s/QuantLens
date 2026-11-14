from types import SimpleNamespace
import pytest

from factor_evolution.engine import EvolutionEngine
from factor_evolution.models import EvolutionConfig, FactorVersion, VersionResult, WindowResult
from factor_evolution.selection import definition_key
from factor_evolution.walk_forward import aggregate_results


def result(net=.1, excess=.04, drawdown=-.1, feasible=True):
    metrics = {'research_standard':'cn_a_daily_v3','input_quality':{'passed':True},
               'returns':{'annualized_return':net,'annualized_excess_return':excess},
               'risk':{'max_drawdown':drawdown},'trading':{'annualized_turnover_oneside':3},
               'objective':{'value':net/abs(drawdown),'eligible':feasible}}
    window=WindowResult(window='probe',run_id='BT-probe',metrics=metrics)
    aggregate=aggregate_results([window],label='probe')
    return VersionResult(version_id='probe',train=aggregate,validation=aggregate,score=excess)


def test_higher_excess_does_not_promote_losing_strategy():
    assert not EvolutionEngine._is_improved(result(net=-.01,excess=.15),result(net=.1,excess=.04))


def test_feasible_risk_adjusted_candidate_beats_raw_return_chasing():
    assert EvolutionEngine._is_improved(result(net=.09,drawdown=-.08),result(net=.12,drawdown=-.3,feasible=False))
    assert not EvolutionEngine._is_improved(result(net=.12,drawdown=-.3,feasible=False),result(net=.09,drawdown=-.08))


def test_profitability_is_a_hard_adoption_gate():
    config=EvolutionConfig(base_factor='probe',profile_name='probe',train_window={'start':'2018-01-01','end':'2021-12-31'},
        validation_window={'start':'2022-01-01','end':'2024-12-31'},test_window={'start':'2025-01-01','end':'2026-01-01'},
        targets={'min_annualized_excess_return':.03,'max_drawdown':.2,'max_annualized_turnover':6,'min_oos_stability':.5})
    losing=result(net=-.01,excess=.1)
    assert not EvolutionEngine._constraints_pass(config,losing.train.metrics,losing.validation.metrics,1,'validation_generalized')
    del losing.train.metrics['returns']['annualized_return']
    assert not EvolutionEngine._is_improved(losing,result())


def test_comments_and_names_do_not_create_a_new_candidate():
    base=FactorVersion(version_id='v0',factor_name='probe',assumption='probe',direction='positive',timescale='daily',
        dependencies=['close'],code='def compute(p): return p["close"]',change_type='baseline',change_summary='baseline')
    duplicate=base.model_copy(update={'version_id':'new','code':'# new comment\ndef compute(p):\n    return p["close"]\n'})
    assert definition_key(base)==definition_key(duplicate)
    assert definition_key(base)!=definition_key(base.model_copy(update={'direction':'negative'}))


def test_auto_targets_come_from_the_locked_protocol(monkeypatch):
    from factor_evolution import autoplan
    monkeypatch.setattr(autoplan,'FactorRegistry',lambda:SimpleNamespace(get=lambda _:SimpleNamespace(
        status='draft',timescale='daily',applicable_markets=['CN-A'],applicable_instruments=['stock'])))
    monkeypatch.setattr(autoplan,'cache_status',lambda:{'default':SimpleNamespace(state='fresh',meta={'build_params':{'start':'1990-01-01','end':'2026-11-13'}})})
    monkeypatch.setattr(autoplan,'_single_factor_profile',lambda _:('probe',20))
    monkeypatch.setattr('backtest.protocol.verify_protocol_or_raise',lambda:SimpleNamespace(objective=SimpleNamespace(constraints=SimpleNamespace(max_drawdown=.17,max_turnover=4))))
    config=autoplan.build_auto_config('probe')
    assert config.targets.max_drawdown==.17 and config.targets.max_annualized_turnover==4


@pytest.mark.parametrize('run_locked_test', [False, True])
@pytest.mark.parametrize('risk_ok', [False, True])
def test_finalist_respects_locked_test_policy(tmp_path, monkeypatch, run_locked_test, risk_ok):
    from factor_evolution.models import ReviewResult
    from factor_evolution.store import EvolutionStore
    from paths import Workspace

    config=EvolutionConfig(base_factor='probe',profile_name='probe',train_window={'start':'2018-01-01','end':'2021-12-31'},
        validation_window={'start':'2022-01-01','end':'2024-12-31'},test_window={'start':'2025-01-01','end':'2026-01-01'},
        targets={'min_annualized_excess_return':.03,'max_drawdown':.2,'max_annualized_turnover':6,'min_oos_stability':.5},
        run_locked_test=run_locked_test,max_iterations=1)
    base=FactorVersion(version_id='v0',factor_name='probe',assumption='probe',direction='positive',timescale='daily',
        dependencies=['close'],code='def compute(p): return p["close"]',change_type='baseline',change_summary='baseline')
    class Services:
        def load_base(self, _): return base
        def begin_experiment(self, *args): return 'EXP-probe'
        def propose(self, *args, **kwargs):
            return base.model_copy(update={'version_id':'v1','code':'def compute(p): return p["close"].rolling(5).mean()'})
        def check_candidate(self, *args, **kwargs): pass
        def evaluate(self, config, version, **kwargs):
            return result(net=.1 if version.version_id=='v0' else .12,drawdown=-.1 if risk_ok else -.3).train
        def record_result(self, *args, **kwargs): pass
        def review(self, *args, **kwargs): return ReviewResult(decision='continue',assessment='probe')
        def record_review(self, *args, **kwargs): pass
        def record_locked_test(self, *args, **kwargs): pass

    store=EvolutionStore(Workspace(tmp_path));engine=EvolutionEngine(store,Services())
    rid=engine.create(config)
    def evaluate(_config,status,version,*args,**kwargs):
        assert risk_ok, 'Risk rejection must not expose validation'
        value=result(net=.1 if version.version_id=='v0' else .12)
        value.version_id=version.version_id
        value.constraints_passed=version.version_id=='v1'
        store.append_event(rid,'version_evaluated',value.model_dump(mode='json'))
        return value
    monkeypatch.setattr(engine,'_evaluate_version',evaluate)
    monkeypatch.setattr(engine,'_test_passes',lambda *args: (True,1.0))
    if not run_locked_test:
        monkeypatch.setattr(engine.locked_tests,'reserve',lambda **_: (_ for _ in ()).throw(AssertionError('Locked test must remain sealed')))
    outcome=engine.run(rid)
    assert outcome['status']['best_version_id']=='v1'
    assert outcome['status']['locked_test_runs']==int(run_locked_test and risk_ok)
    assert any(event['event_type']=='locked_test_reserved' for event in outcome['events'])==(run_locked_test and risk_ok)
