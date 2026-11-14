import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from backtest.config import BacktestConfig
from backtest.metrics import compute_metrics
from backtest.protocol import verify_protocol_or_raise
from backtest.report import BacktestRunStore
from quantlens.backtest_publication import completed_result
from strategy import StrategyProfileStore


@pytest.fixture
def completed(tmp_path, monkeypatch):
    protocol = verify_protocol_or_raise()
    profile = StrategyProfileStore().resolve('research_reversal_5')
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    monkeypatch.delenv('QUANTLENS_BACKTEST_ROOT', raising=False)
    config_dir = tmp_path / 'config'
    config_dir.mkdir()
    (config_dir / 'backtest_protocol.yaml').write_text(yaml.safe_dump(protocol.model_dump(mode='json')))
    config = BacktestConfig(profile_name=profile.profile_name, start='2024-01-02',
                            end='2024-01-05', initial_capital=Decimal('1000'))
    store = BacktestRunStore.create(profile_name=profile.profile_name, run_id='BT-publication-test',
        config_payload={'config': config.model_dump(mode='json'), 'config_hash': config.content_hash(),
                        'profile': profile.model_dump(mode='json'),
                        'protocol': protocol.model_dump(mode='json'),
                        'protocol_hash': protocol.content_hash(), 'protocol_locked': True,
                        'research_standard': 'cn_a_daily_v3',
                        'data_fingerprint': {'data_snapshot': 'frozen-snapshot'}})
    nav = pd.DataFrame({'date': pd.date_range('2024-01-02', periods=4),
        'nav': [1000., 1001., 1002., 1003.], 'cash': [1000.] * 4,
        'position_value': [0.] * 4, 'daily_return': [0., .001, .000999, .000998],
        'benchmark_return': [0.] * 4, 'excess_return': [0., .001, .000999, .000998],
        'turnover_oneside': [0.] * 4, 'buy_amount': [0.] * 4, 'sell_amount': [0.] * 4,
        'cost_paid': [0.] * 4, 'n_holdings': [0] * 4})
    from quantlens.spark_publish_backtests import SCHEMAS
    trades = pd.DataFrame(columns=[part.split()[0] for part in SCHEMAS['trades'].split(',')])
    positions = pd.DataFrame(columns=[part.split()[0] for part in SCHEMAS['positions'].split(',')])
    metrics = compute_metrics(nav, trades, initial_capital=1000,
                              trading_days_per_year=protocol.trading_days_per_year)
    metrics.update(research_standard='cn_a_daily_v3', input_quality={'passed': True})
    for name, frame in [('nav', nav), ('trades', trades), ('positions', positions)]:
        store.write_daily(name, frame)
    store.write_metrics(metrics)
    store.update_status(state='completed')
    return store


def test_valid_current_result_is_normalized_without_mutating_artifacts(completed):
    original = (completed.run_dir / 'metrics.yaml').read_bytes()
    result = completed_result(completed.run_id)
    assert result['snapshot'] == 'frozen-snapshot'
    assert result['frames']['nav']['date'].tolist() == ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05']
    assert (completed.run_dir / 'metrics.yaml').read_bytes() == original


@pytest.mark.parametrize('corrupt', ['metric', 'protocol', 'incomplete', 'date'])
def test_publication_rejects_invalid_results(completed, corrupt):
    if corrupt == 'metric':
        metrics = completed.read_metrics()
        metrics['returns']['total_return'] = 999
        completed.write_metrics(metrics)
    elif corrupt == 'protocol':
        config = completed.read_config()
        config['protocol_hash'] = 'obsolete'
        (completed.run_dir / 'config.yaml').write_text(yaml.safe_dump(config))
    elif corrupt == 'incomplete':
        completed.update_status(state='running')
    else:
        nav = completed.read_daily('nav')
        nav.loc[0, 'date'] = pd.Timestamp('2020-01-01')
        completed.write_daily('nav', nav)
    from exceptions import ContractViolation
    with pytest.raises((ValueError, ContractViolation)):
        completed_result(completed.run_id)


def test_results_api_does_not_fall_back_to_local_artifacts(completed, monkeypatch):
    from webui.backend.routes.backtest import list_runs, get_run
    from fastapi import HTTPException
    monkeypatch.setattr('quantlens.ads.query', lambda *args, **kwargs: [])
    monkeypatch.setattr('quantlens.ads.provenance', lambda *args: {})
    assert list_runs()['items'] == []
    with pytest.raises(HTTPException) as error:
        get_run(completed.run_id)
    assert error.value.status_code == 404


def test_results_list_puts_recent_completions_before_random_run_ids(completed, monkeypatch):
    import json
    from webui.backend.routes.backtest import list_runs
    base = {'strategy_id': 'research_reversal_5', 'config': json.dumps(completed.read_config()),
            'metrics': json.dumps(completed.read_metrics())}
    rows = [{**base, 'backtest_run': run_id, 'status': json.dumps({'state': 'completed', 'updated_at': timestamp})}
            for run_id, timestamp in [('BT-z-old', '2026-11-14T09:00:00+00:00'),
                                      ('BT-a-new', '2026-11-14T10:00:00+00:00')]]
    monkeypatch.setattr('quantlens.ads.query', lambda *args, **kwargs: rows)
    monkeypatch.setattr('quantlens.ads.provenance', lambda *args: {})
    assert [row['run_id'] for row in list_runs()['items']] == ['BT-a-new', 'BT-z-old']


def test_publication_exports_the_current_contract_in_an_isolated_warehouse(completed, monkeypatch):
    import json
    from quantlens.spark_publish_backtests import run
    from quantlens.warehouse import make_run, root_uri, session
    monkeypatch.setenv('QUANTLENS_COMPUTE_LOCAL', '1')
    monkeypatch.setenv('SPARK_LOCAL_IP', '127.0.0.1')
    monkeypatch.setenv('PYSPARK_PYTHON', sys.executable)
    spark = session('backtest-publication-test')
    spark.sparkContext.setLogLevel('ERROR')
    try:
        context = make_run('publication-test', snapshot='frozen-snapshot')
        run(spark, context, [completed.run_id], 'publication-test')
        summary = spark.table('ads.backtest_summary').first().asDict()
        assert summary['backtest_run'] == completed.run_id
        assert json.loads(summary['config']) == completed.read_config()
        assert json.loads(summary['metrics']) == completed.read_metrics()
        assert spark.table('ads.backtest_nav').count() == 4
        assert spark.table('ads.backtest_trades').count() == 0
        assert spark.table('ads.backtest_positions').count() == 0
        manifest_file = next((Path(root_uri()) / 'meta/batches/publication-test/manifest').glob('part-*'))
        manifest = json.loads(manifest_file.read_text())
        assert manifest['scope'] == 'backtest'
        assert manifest['tables']['backtest_nav']['rows'] == 4
        assert manifest['tables']['backtest_summary']['rows'] == 1
        assert manifest['tables']['backtest_trades']['rows'] == 0
    finally:
        spark.stop()


def test_cached_research_is_automatically_discovered_without_read_side_fallback(monkeypatch, tmp_path):
    from unittest.mock import Mock
    from quantlens.research_result_publisher import ResearchResultPublisher
    manager = Mock()
    manager.latest.return_value = None
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    rows = [{'run_id': name, 'state': state} for name, state in [
        ('published', 'completed'), ('cached', 'completed'), ('spark', 'completed'), ('running', 'running')]]
    for row in rows:
        directory = tmp_path / 'data/backtest_runs/profile' / row['run_id']
        directory.mkdir(parents=True)
        (directory / 'status.yaml').write_text(yaml.safe_dump({'state': row['state']}))
    monkeypatch.setattr('quantlens.research_result_publisher.ads.query', lambda *a, **k: [{'backtest_run': 'published'}])
    monkeypatch.setattr('quantlens.research_result_publisher.cluster_status', lambda: {'available': True})
    publish = Mock(return_value='batch-new')
    monkeypatch.setattr('quantlens.research_result_publisher.publish_completed_runs', publish)
    publisher = ResearchResultPublisher(manager)
    publisher.tick()
    callback = manager.start.call_args.args[1]
    reporter = Mock()
    assert callback(reporter) == {'batch': 'batch-new', 'run_ids': ['cached', 'spark']}
    publish.assert_called_once_with(['cached', 'spark'], reporter=reporter)
    manager.reset_mock()
    manager.latest.return_value = {'status': 'running'}
    publisher.tick()
    manager.start.assert_not_called()
    manager.latest.return_value = None
    monkeypatch.setattr('quantlens.research_result_publisher.cluster_status', lambda: {'available': False})
    publisher.tick()
    manager.start.assert_not_called()


def test_background_publisher_does_not_repeat_a_primary_sync(completed, monkeypatch):
    from unittest.mock import Mock
    from quantlens.research_result_publisher import ResearchResultPublisher
    manager = Mock()
    manager.latest.return_value = None
    query = Mock(side_effect=[[], [{'backtest_run': completed.run_id}]])
    monkeypatch.setattr('quantlens.research_result_publisher.ads.query', query)
    monkeypatch.setattr('quantlens.research_result_publisher.cluster_status', lambda: {'available': True})
    publish = Mock()
    monkeypatch.setattr('quantlens.research_result_publisher.publish_completed_runs', publish)
    ResearchResultPublisher(manager).tick()
    assert manager.start.call_args.args[1](Mock()) == {'batch': None, 'run_ids': []}
    publish.assert_not_called()


def test_an_older_export_cannot_remove_a_newly_published_backtest(completed):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from paths import workspace
    from exceptions import ContractViolation
    from quantlens.sync_ads import _publish_manifest
    root = workspace().data / 'ads'
    root.mkdir()
    def entry(name, ids):
        pq.write_table(pa.table({'backtest_run': ids}), root / (name + '.parquet'))
        return {'files': [{'path': name + '.parquet'}]}
    current = entry('current', ['BT-a', 'BT-b'])
    stale = entry('stale', ['BT-a'])
    fresh = entry('fresh', ['BT-a', 'BT-b', 'BT-c'])
    path = workspace().data / 'ads_manifest.json'
    path.write_text(json.dumps({'tables': {'backtest_summary': current, 'screen': {'files': []}}}))
    original = path.read_bytes()
    with pytest.raises(ContractViolation) as error:
        _publish_manifest(path, {'scope': 'backtest', 'tables': {'backtest_summary': stale}})
    assert error.value.code == 'ADS_BACKTEST_EXPORT_STALE'
    assert path.read_bytes() == original
    result = _publish_manifest(path, {'scope': 'backtest', 'tables': {'backtest_summary': fresh}})
    assert result['tables']['backtest_summary'] == fresh
    assert 'screen' in result['tables']


def test_submission_locks_are_reentrant_and_serialize_threads(completed):
    import threading
    import time
    from quantlens.backtest_publication import publication_lock
    order = []
    entered = threading.Event()
    def first():
        with publication_lock():
            with publication_lock():
                order.append('first-start')
                entered.set()
                time.sleep(.03)
                order.append('first-end')
    def second():
        entered.wait()
        with publication_lock():
            order.append('second')
    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=3)
    assert not any(thread.is_alive() for thread in threads)
    assert order == ['first-start', 'first-end', 'second']


def test_positions_api_applies_page_offset_without_changing_selected_date(monkeypatch):
    from webui.backend.routes import backtest

    queries = []
    monkeypatch.setattr(backtest, '_require_valid_run', lambda run_id: {})
    monkeypatch.setattr(backtest.ads, 'provenance', lambda *args: {})
    monkeypatch.setattr(backtest.ads, 'count', lambda *args: 23)

    def query(table, where, params, **kwargs):
        queries.append((table, params, kwargs))
        if table == 'backtest_nav':
            return [{'date': '2024-01-02'}, {'date': '2024-01-03'}]
        return [{'instrument': f'stock-{i}'} for i in range(kwargs['offset'], 23)]

    monkeypatch.setattr(backtest.ads, 'query', query)
    result = backtest.get_positions('BT-test', date='2024-01-02', limit=10, offset=20)
    assert result['date'] == '2024-01-02'
    assert result['dates'] == ['2024-01-02', '2024-01-03']
    assert result['total_rows'] == 23
    assert len(result['items']) == 3
    assert queries[-1] == ('backtest_positions', ['BT-test', '2024-01-02'],
                           {'limit': 10, 'offset': 20, 'order': 'weight DESC,instrument'})


def test_trade_pages_query_newest_dates_before_slicing(tmp_path, monkeypatch):
    from quantlens import ads
    from webui.backend.routes import backtest

    path = tmp_path / 'trades.parquet'
    pd.DataFrame([{'backtest_run': 'BT-page', 'date': f'2024-01-{i:02d}',
                   'instrument': '000001', 'side': 'buy'} for i in range(1, 24)]).to_parquet(path)
    monkeypatch.setattr(ads, 'paths', lambda table: [str(path)])
    monkeypatch.setattr(ads, 'provenance', lambda *args: {})
    monkeypatch.setattr(backtest, '_require_valid_run', lambda run_id: {})
    first = backtest.get_trades('BT-page', limit=10, offset=0)
    last = backtest.get_trades('BT-page', limit=10, offset=20)
    assert first['total_rows'] == 23
    assert [row['date'] for row in first['items']] == [f'2024-01-{i:02d}' for i in range(23,13,-1)]
    assert [row['date'] for row in last['items']] == ['2024-01-03', '2024-01-02', '2024-01-01']


def test_backtest_list_uses_completion_times_instead_of_random_ids(tmp_path, monkeypatch):
    import json
    from quantlens import ads
    from webui.backend.routes.backtest import list_runs

    path = tmp_path / 'summary.parquet'
    rows = [{'backtest_run': run_id, 'strategy_id': 'research',
             'config': json.dumps({'protocol': {'schema_version': 3}}), 'metrics': '{}',
             'status': json.dumps({'state': 'completed', 'updated_at': timestamp})}
            for run_id, timestamp in [('BT-z-old', '2026-11-14T10:00:00+08:00'),
                                      ('BT-a-new', '2026-11-14T03:00:00Z'),
                                      ('BT-missing', None)]]
    pd.DataFrame(rows).to_parquet(path)
    monkeypatch.setattr(ads, 'paths', lambda table: [str(path)])
    monkeypatch.setattr(ads, 'provenance', lambda *args: {})
    result = list_runs()
    assert [row['run_id'] for row in result['items']] == ['BT-a-new', 'BT-z-old', 'BT-missing']
