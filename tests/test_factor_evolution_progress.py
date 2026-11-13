"""Regression coverage for running research evidence and scoped ADS publication."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from exceptions import QuantTrainingError
from factor_evolution.engine import EvolutionEngine
from factor_evolution.models import EvolutionConfig, FactorVersion, WindowResult
from factor_evolution.store import EvolutionStore
from paths import Workspace
from quantlens.spark_ads import BACKTEST_TABLES, export_backtests
from quantlens.sync_ads import _publish_manifest


def config():
    return EvolutionConfig(
        base_factor='probe', profile_name='research_probe',
        train_window={'start': '2018-01-01', 'end': '2021-12-31'},
        validation_window={'start': '2022-01-01', 'end': '2024-12-31'},
        test_window={'start': '2025-01-01', 'end': '2026-01-01'},
        targets={'min_annualized_excess_return': .03, 'max_drawdown': .25, 'max_annualized_turnover': 12, 'min_oos_stability': .5}, max_iterations=1,
    )


def test_baseline_is_linked_before_evaluation_and_windows_are_persisted(tmp_path):
    store = EvolutionStore(Workspace(tmp_path))
    services = Mock()
    services.load_base.return_value = FactorVersion(
        version_id='v000-baseline', factor_name='probe', assumption='test',
        direction='positive', timescale='daily', dependencies=['close'], code='def compute(panel): return panel["close"]',
        change_type='baseline', change_summary='基础版本',
    )
    services.begin_experiment.return_value = 'EXP-baseline'
    observed = []

    def evaluate(_config, version, **kwargs):
        events = store.detail(research_id)['events']
        assert any(e['event_type'] == 'baseline_started' and e['payload']['experiment_id'] == 'EXP-baseline' for e in events)
        observed.append(store.load_status(research_id).progress)
        kwargs['on_progress']('真实执行日志')
        time.sleep(.005)
        return WindowResult(window=kwargs['window_name'], run_id='BT-' + kwargs['window_name'], metrics={
            'returns': {'annualized_excess_return': .05}, 'risk': {'max_drawdown': -.1},
            'trading': {'annualized_turnover_oneside': 2},
        })

    services.evaluate.side_effect = evaluate
    services.propose.side_effect = QuantTrainingError(code='FIXTURE_STOP', message='end fixture')
    engine = EvolutionEngine(store, services)
    research_id = engine.create(config())
    detail = engine.run(research_id)
    assert len([e for e in detail['events'] if e['event_type'] == 'window_completed']) == 2
    assert observed[1] > observed[0]
    assert detail['status']['elapsed_seconds'] > 0
    assert services.record_window_result.call_count == 2
    assert any(e['event_type'] == 'runtime_log' and e['payload']['message'] == '真实执行日志' for e in detail['events'])
    assert all('test' not in call.kwargs['window_name'] for call in services.evaluate.call_args_list)


def test_backtest_export_does_not_rebuild_serving_or_drop_older_runs():
    spark = Mock()
    spark.table.return_value.count.return_value = 12
    with patch('quantlens.spark_ads.build') as rebuild, patch('quantlens.spark_ads.write_json'), patch('quantlens.spark_ads.root_uri', return_value='/fixture'):
        result = export_backtests(spark, {'run_id': 'job', 'data_snapshot': 'snapshot'}, 'batch-test', '2021-12-31')
    rebuild.assert_not_called()
    assert set(result['tables']) == set(BACKTEST_TABLES)
    spark.table.return_value.where.assert_not_called()
    assert result['scope'] == 'backtest'


def test_partial_manifest_preserves_market_data_identity_and_unrelated_tables(tmp_path):
    path = tmp_path / 'manifest.json'
    previous = {'run_id': 'daily-job', 'data_as_of': '2026-11-13', 'tables': {'screen': {'files': ['screen']}, 'backtest_nav': {'files': ['old']}}}
    path.write_text(json.dumps(previous))
    result = _publish_manifest(path, {'scope': 'backtest', 'run_id': 'backtest-job', 'data_as_of': '2021-12-31', 'tables': {'backtest_nav': {'files': ['new']}}})
    assert result['data_as_of'] == '2026-11-13'
    assert result['run_id'] == 'daily-job'
    assert result['tables']['screen'] == previous['tables']['screen']
    assert result['tables']['backtest_nav'] == {'files': ['new']}


def test_session_ids_include_legacy_baseline_and_exclude_next_session():
    from webui.backend.routes.factor_evolution import _session_experiment_ids
    detail = {'config': {'base_factor': 'probe', 'profile_name': 'research_probe'}, 'status': {},
              'events': [{'event_type': 'session_created', 'created_at': '2026-11-13T13:00:00+00:00', 'payload': {}}]}
    rows = [dict(exp_id='baseline', parent_exp_id=None, profile_name='research_probe', created_at='2026-11-13T13:00:01+00:00'),
            dict(exp_id='next', parent_exp_id=None, profile_name='research_probe', created_at='2026-11-13T14:00:01+00:00')]
    with patch('webui.backend.routes.factor_evolution.EvolutionStore') as store, patch('experiments.store.ExperimentStore') as experiments:
        store.return_value.list.return_value = [dict(research_id='next-session', base_factor='probe', started_at='2026-11-13T14:00:00+00:00')]
        experiments.return_value.list_experiments.return_value = rows
        assert _session_experiment_ids('current', detail) == ['baseline']
        experiments.return_value.list_experiments.return_value = [rows[0], {**rows[0], 'exp_id': 'ambiguous'}]
        assert _session_experiment_ids('current', detail) == []
    detail['events'].append({'event_type': 'baseline_started', 'payload': {'experiment_id': 'explicit'}})
    assert _session_experiment_ids('current', detail) == ['explicit']


def test_cancellation_survives_an_older_worker_status(tmp_path):
    store = EvolutionStore(Workspace(tmp_path))
    research_id = store.create(config()).research_id
    worker_status = store.load_status(research_id)
    store.request_cancel(research_id)
    worker_status.phase = 'window completed'
    store.save_status(worker_status)
    assert store.load_status(research_id).cancel_requested


def test_batch_job_shares_session_and_checks_cancellation_between_windows(tmp_path):
    from quantlens.spark_backtest import run_windows
    control = tmp_path / 'status.json'
    control.write_text(json.dumps({'cancel_requested': False}))
    requests = [{'window': f'wf00{i}_train', 'run_id': f'BT-{i}', 'config': {
        'profile_name': 'probe', 'start': '2020-01-01', 'end': '2021-01-01', 'initial_capital': 1000000,
    }} for i in range(1, 4)]
    spark, context = object(), {'run_id': 'batch'}

    def completed(*args):
        assert args[:2] == (spark, context)
        control.write_text(json.dumps({'cancel_requested': True}))
        return type('Outcome', (), {'run_id': args[6]})()

    with patch('quantlens.spark_backtest.run', side_effect=completed) as run:
        outcomes = run_windows(spark, context, requests, {'profile': 'same'}, {'protocol': 'same'}, control_path=control)
        assert len(outcomes) == 1
        run.assert_called_once_with(spark, context, 'probe', '2020-01-01', '2021-01-01', 1000000,
                                    'BT-1', {'profile': 'same'}, {'protocol': 'same'}, requests[0]['config'])
    with patch('quantlens.spark_backtest.run') as run:
        assert run_windows(spark, context, requests, {}, {}, deadline=time.time()-1) == []
        run.assert_not_called()


def test_batch_engine_records_every_window_and_does_not_expose_test(tmp_path):
    from factor_evolution.models import WalkForwardConfig
    settings = config().model_copy(update={'walk_forward': WalkForwardConfig(enabled=True)})
    store = EvolutionStore(Workspace(tmp_path))
    status = store.create(settings)
    status.progress = .01
    version = FactorVersion(version_id='v000-baseline', factor_name='probe', assumption='test',
                            direction='positive', timescale='daily', dependencies=['close'], code='def compute(p): return p["close"]',
                            change_type='baseline', change_summary='baseline')
    observed = []

    class Services:
        record_result = Mock()
        record_window_result = Mock()

        def evaluate_windows(self, cfg, ver, **kwargs):
            assert ver == version and cfg == settings
            results = {}
            for name, window in kwargs['windows']:
                assert window.end <= settings.validation_window.end
                kwargs['on_window_started'](name)
                result = WindowResult(window=name, run_id='BT-'+name, metrics={
                    'returns': {'annualized_excess_return': .05}, 'risk': {'max_drawdown': -.1},
                    'trading': {'annualized_turnover_oneside': 2}})
                kwargs['on_window_completed'](name, result)
                observed.append(store.load_status(status.research_id).progress)
                results[name] = result
            return results

    engine = EvolutionEngine(store, Services())
    result = engine._evaluate_version(settings, status, version, 'EXP-baseline', time.monotonic(), 0, lambda _: None, baseline=True)
    assert len(result.folds) == 3 and result.score == .05
    assert len(observed) == 6 and observed == sorted(observed)
    assert len([e for e in store.detail(status.research_id)['events'] if e['event_type']=='window_completed']) == 6


def test_production_batch_streams_verified_local_results_and_syncs_once():
    from types import SimpleNamespace
    from factor_evolution.services import ProductionEvolutionServices
    from factor_evolution.walk_forward import build_folds
    from factor_evolution.models import WalkForwardConfig
    settings = config().model_copy(update={'walk_forward': WalkForwardConfig(enabled=True)})
    version = FactorVersion(version_id='v0', factor_name='probe', assumption='test', direction='positive',
                            timescale='daily', dependencies=['close'], code='def compute(p): return p["close"]',
                            change_type='baseline', change_summary='baseline')
    service = ProductionEvolutionServices()
    profile = Mock(profile_name='research_probe')
    profile.model_copy.return_value = profile
    profile.model_dump.return_value = {'profile_name': 'research_probe'}
    protocol = Mock()
    protocol.model_dump.return_value = {'locked': True}
    service._context = Mock(return_value=(protocol, profile))
    service._published_candidates[version.version_id + ':' + version.code_hash] = 'published_probe'
    windows = [(f'{fold.fold_id}_{kind}', window) for fold in build_folds(settings)
               for kind, window in [('train', fold.train_window), ('validation', fold.validation_window)]]
    phases, completed = [], []
    record = Mock()
    record.read_status.return_value = {'state': 'completed'}
    record.read_metrics.return_value = {'returns': {'annualized_excess_return': .05}}

    def submit(module, *args, reporter):
        assert module == 'spark_backtest'
        requests = json.loads(args[args.index('--windows') + 1])
        assert len(requests) == 6
        assert all(item['config']['end'] <= settings.validation_window.end for item in requests)
        for item in requests:
            reporter('QUANTLENS_WINDOW_STARTED=' + json.dumps({'window': item['window']}))
            reporter('QUANTLENS_WINDOW_COMPLETED=' + json.dumps({'window': item['window'], 'run_id': item['run_id']}))

    with patch('quantlens.compute.submit', side_effect=submit) as dispatch, patch('quantlens.sync_ads.sync') as sync, patch('backtest.report.BacktestRunStore.open_run', return_value=record):
        results = service.evaluate_windows(settings, version, windows=windows, research_id='EVO-test', timeout_seconds=60,
                                           on_progress=lambda _: None, on_window_started=phases.append,
                                           on_window_completed=lambda name, result: completed.append((name, result)))
    assert len(results) == len(phases) == len(completed) == 6
    dispatch.assert_called_once()
    sync.assert_called_once()


def test_equivalent_direction_metadata_and_comments_do_not_trigger_backtests():
    import pytest
    from ai.schemas import FactorCandidate
    from factor_evolution.services import ProductionEvolutionServices, _candidate_parent, _SYSTEM_PROPOSE
    current = FactorVersion(version_id='v0', factor_name='probe', assumption='test', direction='positive',
                            timescale='daily', dependencies=['close'], code='def compute(p):\n    return p["close"]\n',
                            change_type='baseline', change_summary='baseline')
    candidate = FactorCandidate(factor_name='probe', display_name='探针', assumption='test', direction='positive',
                                timescale='daily', dependencies=['close'], code=current.code+'# metadata only\n',
                                parameters={'_change_summary': '规范方向枚举'}, model_name='test-model')
    assert _candidate_parent(current)['direction'] == 'positive'
    assert 'long_only_positive' not in _SYSTEM_PROPOSE
    for direction in ['positive', 'long_only_positive']:
        with pytest.raises(QuantTrainingError) as exc:
            ProductionEvolutionServices._require_single_change(current.model_copy(update={'direction': direction}), candidate, 'direction')
        assert exc.value.code == 'EVOLUTION_NO_EFFECTIVE_CHANGE'
    flipped = candidate.model_copy(update={'direction': 'negative', 'code': current.code})
    ProductionEvolutionServices._require_single_change(current, flipped, 'direction')


def test_backtest_identity_is_not_overwritten_by_publication_lineage():
    from webui.backend.routes.backtest import get_run
    row = {'backtest_run': 'BT-12345678901234567890', 'strategy_id': 'research_probe',
           'config': json.dumps({'profile': {'factors': {'items': []}}}),
           'status': json.dumps({'state': 'completed'}), 'metrics': json.dumps({'returns': {}})}
    with patch('webui.backend.routes.backtest.ads.query', return_value=[row]), patch('webui.backend.routes.backtest.ads.provenance', return_value={'run_id': 'publication-job', 'data_snapshot': 'snapshot'}):
        detail = get_run(row['backtest_run'])
    assert detail['run_id'] == row['backtest_run']
    assert detail['publication_run_id'] == 'publication-job'
    assert detail['data_snapshot'] == 'snapshot'


def test_candidate_json_errors_retry_within_round_but_transport_errors_do_not():
    from types import SimpleNamespace
    from ai.schemas import FactorCandidate
    from exceptions import ModelRouteError
    from factor_evolution.services import ProductionEvolutionServices
    import pytest
    current = FactorVersion(version_id='v0', factor_name='probe', assumption='test', direction='positive',
                            timescale='daily', dependencies=['close'], code='def compute(p): return p["close"]',
                            change_type='baseline', change_summary='baseline')
    candidate = FactorCandidate(factor_name='probe', display_name='探针', assumption='test', direction='negative',
                                timescale='daily', dependencies=['close'], code=current.code, model_name='test-model',
                                parameters={'_evolution_change_type':'direction','_change_summary':'反转方向','_expected_impact':'验证反转假设'})
    with patch('ai.router.AIRouter') as router, patch('proposals.ProposalStore') as proposals:
        router.return_value.pick.return_value = SimpleNamespace(name='test-model')
        router.return_value.run_task.side_effect = [ModelRouteError(code='AI_OUTPUT_NOT_JSON', message='invalid JSON'), candidate.model_dump(mode='json')]
        proposals.return_value.create.return_value = (SimpleNamespace(proposal_id='P-test'), None)
        result = ProductionEvolutionServices().propose(config(), current, [], iteration=1, timeout_seconds=30)
        assert result.generation_attempts == 2
        assert router.return_value.run_task.call_count == 2
        assert 'AI_OUTPUT_NOT_JSON' in router.return_value.run_task.call_args.kwargs['prompt']
        router.return_value.run_task.reset_mock()
        router.return_value.run_task.side_effect = ModelRouteError(code='AI_HTTP_STATUS_ERROR', message='unauthorized')
        with pytest.raises(ModelRouteError):
            ProductionEvolutionServices().propose(config(), current, [], iteration=1, timeout_seconds=30)
        assert router.return_value.run_task.call_count == 1


def test_public_factor_catalog_excludes_internal_iteration_copies():
    from types import SimpleNamespace
    from webui.backend.routes.factors import list_factors
    base = 'smooth_uptrend_efficiency_60d'
    copy = base + '_r_0123456789ab'
    rows = [{'metadata': json.dumps({'name': name, 'version': 'v1', 'status': 'draft'}),
             'run_id': 'publication', 'data_snapshot': 'snapshot', 'code_hash': 'hash'} for name in [base, copy]]
    with patch('webui.backend.routes.factors.FactorRegistry') as registry, patch('quantlens.ads.query', side_effect=lambda table, *args, **kwargs: rows if table == 'factor_meta' else []), patch('quantlens.ads.provenance', return_value={}):
        registry.return_value.list.return_value = [SimpleNamespace(name=name, version='v1') for name in [base, copy]]
        result = list_factors()
    assert [row['name'] for row in result['items']] == [base]
