import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from exceptions import ContractViolation, QuantTrainingError
from factor_evolution.supervision import audit_factor, check_source


@pytest.fixture
def panel():
    rng = np.random.default_rng(42)
    close = pd.DataFrame(rng.normal(size=(100, 12)),
                         index=pd.bdate_range('2020-01-01', periods=100))
    return {'close': close}


def test_trailing_factor_passes(panel):
    result = audit_factor('def compute(p): return p["close"].rolling(10).mean().rank(axis=1)',
                          panel, min_history=15)
    assert result['passed'] and result['prefix_checks'] == 3


@pytest.mark.parametrize('expression', [
    'p["close"].shift(-1)', 'p["close"].shift(periods=-2)',
    'p["close"].rolling(10, center=True).mean()', 'p["close"].bfill()',
])
def test_obvious_lookahead_rejected(expression):
    with pytest.raises(ContractViolation) as error:
        check_source('def compute(p): return ' + expression)
    assert error.value.code == 'EVOLUTION_CAUSALITY_FAILED'


@pytest.mark.parametrize('source', [
    'def compute(p): return p["close"] / p["close"].mean()',
    'def compute(p):\n n = -1\n return p["close"].shift(n)',
    'def compute(p): return p["close"].expanding().mean()',
    'def compute(p): return p["close"].iloc[::-1]',
    'def compute(p):\n p["close"].iloc[0,0] = 0\n return p["close"]',
])
def test_dynamic_leaks_unbounded_history_and_bad_contracts_rejected(panel, source):
    with pytest.raises(ContractViolation):
        audit_factor(source, panel, min_history=15)


def test_insufficient_audit_window_fails_closed(panel):
    with pytest.raises(ContractViolation):
        audit_factor('def compute(p): return p["close"]', panel, min_history=100)


def test_production_rejects_lookahead_before_publishing(monkeypatch):
    from types import SimpleNamespace
    from factor_evolution.services import ProductionEvolutionServices
    import quantlens.research_candidate

    published = []
    monkeypatch.setattr(quantlens.research_candidate, 'publish_candidate',
                        lambda *args, **kwargs: published.append(True))
    with pytest.raises(ContractViolation) as error:
        ProductionEvolutionServices().check_candidate(
            None, SimpleNamespace(code='def compute(p): return p["close"].shift(-1)'),
            timeout_seconds=10)
    assert error.value.code == 'EVOLUTION_CAUSALITY_FAILED'
    assert not published


def test_resumed_supervision_clears_previous_terminal_status(tmp_path):
    from factor_evolution.engine import EvolutionEngine
    from factor_evolution.models import EvolutionConfig, EvolutionState
    from factor_evolution.store import EvolutionStore
    from paths import Workspace

    config = EvolutionConfig(base_factor='probe', profile_name='probe',
        train_window={'start': '2018-01-01', 'end': '2021-12-31'},
        validation_window={'start': '2022-01-01', 'end': '2024-12-31'},
        test_window={'start': '2025-01-01', 'end': '2026-01-01'},
        targets={'min_annualized_excess_return': .03, 'max_drawdown': .2,
                 'max_annualized_turnover': 6, 'min_oos_stability': .5})
    store = EvolutionStore(Workspace(tmp_path))
    status = store.create(config)
    status.state = EvolutionState.FAILED
    status.finished_at = '2026-01-01T00:00:00+00:00'
    status.terminal_reason = 'previous failure'
    store.save_status(status)

    class Services:
        def load_base(self, _config):
            current = store.load_status(status.research_id)
            assert current.state == EvolutionState.RUNNING
            assert current.finished_at is None and not current.terminal_reason
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        EvolutionEngine(store, Services()).run(status.research_id)


def test_production_dynamic_audit_uses_training_only_and_reuses_panel(monkeypatch):
    from types import SimpleNamespace
    from factor_evolution.models import EvolutionConfig, FactorVersion
    from factor_evolution.services import ProductionEvolutionServices
    from factors.registry import FactorRegistry
    import quantlens.research_data

    config = EvolutionConfig(base_factor='probe', profile_name='probe',
        train_window={'start': '2018-01-01', 'end': '2021-12-31'},
        validation_window={'start': '2022-01-01', 'end': '2024-12-31'},
        test_window={'start': '2025-01-01', 'end': '2026-01-01'},
        targets={'min_annualized_excess_return': .03, 'max_drawdown': .2,
                 'max_annualized_turnover': 6, 'min_oos_stability': .5})
    version = FactorVersion(version_id='v1', factor_name='probe', assumption='probe',
        direction='positive', timescale='daily', dependencies=['close'],
        code='def compute(p): return p["close"].rolling(10).mean()',
        change_type='formula', change_summary='probe')
    calls = []

    def extract(query):
        calls.append(query)
        dates = pd.bdate_range(query['start'], query['end'])
        return pd.DataFrame({'date': dates, 'code': '000001',
                             'close': np.arange(len(dates), dtype=float)})

    monkeypatch.setattr(FactorRegistry, 'get', lambda *args: SimpleNamespace(min_history=15))
    monkeypatch.setattr(quantlens.research_data, 'extract', extract)
    service = ProductionEvolutionServices()
    service._audit_candidate(config, version)
    assert calls[0]['end'] == config.train_window.end
    assert calls[0]['start'] >= config.train_window.start
    # A dynamic full-series mean is invisible to the simple syntax checker.
    bad = version.model_copy(update={'version_id': 'v2',
        'code': 'def compute(p): return p["close"] / p["close"].mean()'})
    with pytest.raises(ContractViolation):
        service._audit_candidate(config, bad)
    assert len(calls) == 1
    assert service._candidate_audits['v1:' + version.code_hash]['training_only']
    assert service._candidate_audits['v1:' + version.code_hash]['dates'] <= 28


def test_production_audit_rejects_export_outside_training_window(monkeypatch):
    from types import SimpleNamespace
    from factor_evolution.services import ProductionEvolutionServices
    from factors.registry import FactorRegistry
    import quantlens.research_data

    config = SimpleNamespace(base_factor='probe',
        train_window=SimpleNamespace(start='2020-01-01', end='2021-12-31'),
        content_hash=lambda: 'probe')
    version = SimpleNamespace(dependencies=['close'])
    monkeypatch.setattr(FactorRegistry, 'get', lambda *args: SimpleNamespace(min_history=15))
    monkeypatch.setattr(quantlens.research_data, 'extract', lambda _: pd.DataFrame(
        {'date': ['2025-01-01'], 'code': ['000001'], 'close': [1.]}))
    with pytest.raises(ContractViolation) as error:
        ProductionEvolutionServices()._audit_candidate(config, version)
    assert error.value.code == 'EVOLUTION_AUDIT_INPUT_INVALID'


def test_missing_clusters_cannot_silently_use_unknown_group(panel):
    from quantlens.research_preflight import require_style_panel

    scores = panel['close']
    clusters = scores * np.nan
    with pytest.raises(ContractViolation) as error:
        require_style_panel(clusters, scores)
    assert error.value.code == 'RESEARCH_STYLE_COVERAGE_MISSING'
    # A single missing date must also block a long otherwise-complete history.
    clusters[:] = 1.
    clusters.iloc[50] = np.nan
    with pytest.raises(ContractViolation):
        require_style_panel(clusters, scores)
    clusters.iloc[50] = 2.
    require_style_panel(clusters, scores)
    clusters.iloc[50, 0] = np.nan
    with pytest.raises(ContractViolation) as error:
        require_style_panel(clusters, scores)
    assert error.value.code == 'RESEARCH_STYLE_STOCKS_MISSING'
    with pytest.raises(ContractViolation):
        require_style_panel(clusters.iloc[:0], scores.iloc[:0])


def test_style_data_for_unscored_stocks_does_not_pass(panel):
    from quantlens.research_preflight import require_style_panel

    scores = panel['close'].copy()
    scores.iloc[:, 6:] = np.nan
    clusters = scores * np.nan
    clusters.iloc[:, 6:] = 1.
    with pytest.raises(ContractViolation):
        require_style_panel(clusters, scores)


def test_preflight_never_opens_test_and_failed_check_is_not_cached(monkeypatch):
    from types import SimpleNamespace
    from factor_evolution.services import ProductionEvolutionServices
    import quantlens.compute

    config = SimpleNamespace(train_window=SimpleNamespace(start='2016-01-01'),
        validation_window=SimpleNamespace(end='2024-12-31'),
        content_hash=lambda: 'probe')
    calls = []

    def submit(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise ContractViolation(code='RESEARCH_STYLE_COVERAGE_MISSING', message='missing')

    monkeypatch.setattr(quantlens.compute, 'submit', submit)
    service = ProductionEvolutionServices()
    with pytest.raises(ContractViolation):
        service._ensure_research_inputs(config)
    service._ensure_research_inputs(config)
    service._ensure_research_inputs(config)
    assert len(calls) == 2
    assert calls[0] == ('spark_research_preflight', '--start', '2016-01-01',
                         '--end', '2024-12-31')


def test_backtest_gate_runs_before_tradability_and_output_creation(panel, monkeypatch):
    from types import SimpleNamespace
    from backtest.config import BacktestConfig
    from backtest.engine import run_backtest
    import backtest.engine

    monkeypatch.setattr(backtest.engine, 'build_tradability',
                        lambda *a, **k: pytest.fail('Should stop before matching'))
    payload = {'signal': SimpleNamespace(scores=panel['close']), 'market': {},
               'clusters': panel['close'] * np.nan}
    with pytest.raises(ContractViolation) as error:
        run_backtest(BacktestConfig(profile_name='probe', start='2020-01-01',
            end='2020-12-31'),
            protocol=SimpleNamespace(price_anchor='next_close', market_rule_name='CN-A-stock', benchmark='same_cluster_equal_weight'),
            profile=SimpleNamespace(), warehouse_inputs=payload)
    assert error.value.code == 'RESEARCH_STYLE_COVERAGE_MISSING'


def test_results_without_current_standard_cannot_be_accepted():
    from quantlens.research_preflight import assess_input_quality
    from factor_evolution.walk_forward import aggregate_results
    from factor_evolution.models import WindowResult
    metrics = {'returns':{'annualized_excess_return':.1},'risk':{'max_drawdown':-.1},
               'trading':{'annualized_turnover_oneside':2},'input_quality':{'passed':True}}
    assert not assess_input_quality(metrics)['passed']
    result = WindowResult(window='train',run_id='BT-old',metrics=metrics)
    assert not aggregate_results([result],label='train').metrics['input_quality']['passed']
    metrics['research_standard'] = 'cn_a_daily_v3'
    assert assess_input_quality(metrics)['passed']
    metrics['input_quality']['passed'] = False
    assert not assess_input_quality(metrics)['passed']


def test_liquidation_day_cannot_hide_benchmark_fallback(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    from rich.console import Console
    from backtest.config import BacktestConfig
    from backtest.engine import run_backtest
    from backtest.protocol import verify_protocol_or_raise
    from backtest.signal import SignalResult
    from strategy import StrategyProfileStore
    from market_rules import MarketRuleRegistry
    import backtest.engine
    import portfolio.construct

    profile = StrategyProfileStore().resolve('research_reversal_5')
    protocol = verify_protocol_or_raise().model_copy(update={"benchmark": "same_cluster_equal_weight"})
    rule = MarketRuleRegistry().get(protocol.market_rule_name)
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    monkeypatch.setattr(backtest.engine, 'MarketRuleRegistry',
                        lambda: SimpleNamespace(get=lambda _: rule))
    dates = pd.bdate_range('2024-01-02', periods=4)
    close = pd.DataFrame(10., index=dates, columns=['000001', '000002'])
    scores, clusters = close.copy(), close * 0
    scores.loc[dates[1], '000001'] = np.nan
    clusters.loc[dates[1], '000001'] = np.nan
    targets = iter([pd.DataFrame({'code': ['000001'], 'weight': [.5]}),
                    pd.DataFrame(columns=['code', 'weight'])])
    monkeypatch.setattr(portfolio.construct, 'construct', lambda *args, **kwargs: next(targets))
    monkeypatch.setattr(backtest.engine, '_signal_days', lambda *args: set(dates[:2]))
    market = {'close': close, 'high': close * 1.01, 'low': close * .99,
              'ret_wd': close * 0, 'is_trading': close.notna(),
              'listed_days': close * 0 + 500, 'trading_status': close * 0 + 1, 'is_delisting': close.notna() & False}
    payload = {'market': market, 'signal': SignalResult(scores, {}, {}),
               'clusters': clusters, 'industries': close * 0, 'mask': close.notna(),
               'breadth': pd.Series(.8, index=dates),
               'delisting_dates': pd.Series(pd.NaT, index=close.columns)}
    from exceptions import BacktestError
    with pytest.raises(BacktestError) as error:
        run_backtest(BacktestConfig(profile_name=profile.profile_name,
            start=str(dates[0].date()), end=str(dates[-1].date())),
            protocol=protocol, profile=profile, warehouse_inputs=payload,
            console=Console(file=io.StringIO()))
    assert error.value.code == 'BACKTEST_HOLDING_CLASSIFICATION_MISSING'


def test_candidate_after_resume_cannot_bypass_preflight(monkeypatch):
    from types import SimpleNamespace
    from factor_evolution.services import ProductionEvolutionServices

    service = ProductionEvolutionServices()
    def reject(_config):
        raise ContractViolation(code='RESEARCH_STYLE_COVERAGE_MISSING', message='missing')
    monkeypatch.setattr(service, '_ensure_research_inputs', reject)
    monkeypatch.setattr(service, '_audit_candidate',
                        lambda *args: pytest.fail('Preflight must run before the audit/publication'))
    version = SimpleNamespace(code='def compute(p): return p["close"]',
        dependencies=['close'], version_id='v1', code_hash='probe')
    with pytest.raises(ContractViolation) as error:
        service.check_candidate(None, version, timeout_seconds=10)
    assert error.value.code == 'RESEARCH_STYLE_COVERAGE_MISSING'


def test_engine_stops_after_first_bad_window_before_proposing(tmp_path):
    from factor_evolution.engine import EvolutionEngine
    from factor_evolution.models import EvolutionConfig, FactorVersion, WindowResult
    from factor_evolution.store import EvolutionStore
    from paths import Workspace

    config = EvolutionConfig(base_factor='probe', profile_name='probe',
        train_window={'start': '2018-01-01', 'end': '2021-12-31'},
        validation_window={'start': '2022-01-01', 'end': '2024-12-31'},
        test_window={'start': '2025-01-01', 'end': '2026-01-01'},
        targets={'min_annualized_excess_return': .03, 'max_drawdown': .2,
                 'max_annualized_turnover': 6, 'min_oos_stability': .5})
    calls = []
    class Services:
        def load_base(self, _):
            return FactorVersion(version_id='v0', factor_name='probe', assumption='probe',
                direction='positive', timescale='daily', dependencies=['close'],
                code='def compute(p): return p["close"]', change_type='baseline', change_summary='probe')
        def begin_experiment(self, *args):
            return 'EXP-probe'
        def check_candidate(self, *args, **kwargs):
            pass
        def evaluate(self, *args, **kwargs):
            calls.append(kwargs['window_name'])
            return WindowResult(window=kwargs['window_name'], run_id='BT-probe', metrics={
                'window': {'trading_days': 2},
                'benchmark': {'name': 'same_cluster_equal_weight',
                              'fallback_dates': ['2020-01-02']},
                'returns': {'annualized_excess_return': .5}})
        def propose(self, *args, **kwargs):
            pytest.fail('No candidate may be generated after a bad baseline window')
    store = EvolutionStore(Workspace(tmp_path))
    rid = store.create(config).research_id
    with pytest.raises(QuantTrainingError) as error:
        EvolutionEngine(store, Services()).run(rid)
    assert error.value.code == 'EVOLUTION_INPUT_QUALITY_INVALID'
    assert len(calls) == 1
    assert store.load_status(rid).state == 'failed'
    assert store.load_status(rid).locked_test_runs == 0


def test_spark_batch_stops_on_quality_failure_before_next_window():
    from types import SimpleNamespace
    from unittest.mock import patch
    from quantlens.spark_backtest import run_windows

    windows = [{'window': name, 'run_id': 'BT-' + name, 'config': {
        'profile_name': 'probe', 'start': '2020-01-01', 'end': '2020-12-31',
        'initial_capital': 1000000}}
        for name in ('train', 'validation')]
    with patch('quantlens.spark_backtest.run', return_value=SimpleNamespace(
            run_id='BT-train', metrics={'input_quality': {'passed': False}})) as run:
        result = run_windows(object(), {}, windows, {}, {})
    assert run.call_count == 1 and len(result) == 1
