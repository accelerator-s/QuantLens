import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from benchmarks.models import BenchmarkPlan, MarketReference, ReferenceComparison
from benchmarks.catalogue import catalogue, validate_plan
from benchmarks.comparison import comparison
from benchmarks.returns import prepare
from exceptions import ContractViolation
from providers.markets.cn_a.csmar.client import CSMARClient
from providers.markets.cn_a.csmar.mapper import CSMARMapper


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    root = tmp_path / 'data/staged/cn_a/stock'
    root.mkdir(parents=True)
    basic = pd.DataFrame([
        {'index_code': '000985', 'name': '中证全指指数', 'asset_type': '1', 'publisher': '3'},
        {'index_code': 'H00300', 'name': '沪深300全收益指数', 'asset_type': '1', 'publisher': '3'},
        {'index_code': 'H00905', 'name': '中证小盘500全收益指数', 'asset_type': '1', 'publisher': '3'},
    ])
    basic.to_parquet(root / 'index_basic.parquet', index=False)
    days = ['2023-12-29', '2024-01-02', '2024-01-03']
    rows = [{'index_code': code, 'date': day, 'close': close, 'name': code}
            for code, values in [('000985', [100, 105, 110]), ('H00300', [100, 110, 99]), ('H00905', [100, 100, 110])]
            for day, close in zip(days, values)]
    pd.DataFrame(rows).to_parquet(root / 'index_daily.parquet', index=False)
    dates = pd.DatetimeIndex(days[1:])
    plan = BenchmarkPlan(return_benchmark={'kind': 'universe_equal_weight'}, market_reference='000985', rationale='股票池研究')
    return root, dates, plan


def test_csmar_index_exports_read_every_archive_part_and_preserve_codes():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as archive:
        archive.writestr('IDX_Idxtrd_1.txt', 'Indexcd\tIdxtrd01\tIdxtrd05\n000985\t2024-01-02\t100\n')
        archive.writestr('IDX_Idxtrd_2.txt', 'Indexcd\tIdxtrd01\tIdxtrd05\nH00300\t2024-01-02\t200\n')
        archive.writestr('IDX_Idxtrd[DES][txt].txt', 'description')
    frame = CSMARClient()._parse_zip(raw.getvalue(), 'IDX_Idxtrd')
    assert frame.Indexcd.tolist() == ['000985', 'H00300']


def test_mapper_keeps_indices_separate_and_converts_percent_once():
    raw = pd.DataFrame({'Indexcd': ['000985', 'H00300'], 'Idxtrd01': ['2024-01-02'] * 2,
                        'Idxtrd05': [100., 200.], 'Idxtrd08': [1.5, -.2], 'Idxtrd09': ['全指', '300全收益']})
    frame = CSMARMapper().to_standard(raw, 'index_daily')
    assert frame.index_code.tolist() == ['000985', 'H00300']
    assert frame.daily_return.tolist() == [.015, -.002]
    assert 'instrument_id' not in frame and not CSMARMapper().validate_schema(frame, 'index_daily')


def test_price_index_can_be_market_reference_but_not_return_benchmark(data):
    root, dates, plan = data
    validate_plan(plan, dates)
    plan.return_benchmark.kind = 'index'
    plan.return_benchmark.code = '000985'
    with pytest.raises(ContractViolation) as error:
        validate_plan(plan, dates)
    assert error.value.code == 'BENCHMARK_TOTAL_RETURN_REQUIRED'


def test_ambiguous_csmar_codes_are_not_silently_combined(data):
    root, dates, plan = data
    frame = pd.read_parquet(root / 'index_basic.parquet')
    frame = pd.concat([frame, pd.DataFrame([{'index_code': '000985', 'name': '另一发行机构的指数', 'asset_type': '1', 'publisher': '2'}])])
    frame.to_parquet(root / 'index_basic.parquet', index=False)
    assert all(row['ambiguous_code'] and not row['domestic_equity'] for row in catalogue() if row['index_code'] == '000985')
    with pytest.raises(ContractViolation):
        validate_plan(plan, dates)


def test_blend_respects_monthly_weight_drift(data):
    _, dates, plan = data
    plan.return_benchmark = type(plan.return_benchmark)(kind='blend', components=[{'code': 'H00300', 'weight': .5}, {'code': 'H00905', 'weight': .5}], rebalance='monthly')
    values, meta = prepare(plan, {}, pd.DataFrame(), pd.DataFrame(), dates)
    assert values.iloc[0] == pytest.approx(.05)
    assert values.iloc[1] == pytest.approx(-.005 / 1.05)
    assert len(meta['index_data_hashes']) == 2


def test_float_cap_weights_use_prior_day(data):
    _, dates, plan = data
    plan.return_benchmark.kind = 'universe_float_cap_weight'
    index = pd.DatetimeIndex(['2023-12-29', *dates.strftime('%Y-%m-%d')])
    caps = pd.DataFrame([[90, 10], [10, 90], [50, 50]], index=index, columns=['a', 'b'])
    returns = pd.DataFrame([[0, 0], [.1, 0], [0, .1]], index=index, columns=['a', 'b'])
    values, _ = prepare(plan, {'market': {'circ_mv': caps}}, returns, returns.notna(), dates)
    assert values.tolist() == pytest.approx([.09, .09])


def test_comparison_uses_prior_close_and_never_fills_quote_gaps(data):
    root, dates, plan = data
    reference = MarketReference(code=plan.market_reference, rationale=plan.rationale, selection_timing='before_backtest')
    nav = pd.DataFrame({'date': dates, 'nav': [110, 121]})
    result = comparison(reference, nav, 100)
    assert result['total_return'] == pytest.approx(.1)
    assert result['excess_return'] == pytest.approx(.11)
    ReferenceComparison.model_validate(result)
    prices = pd.read_parquet(root / 'index_daily.parquet')
    prices = prices[~(prices.index_code.eq('000985') & prices.date.eq('2024-01-02'))]
    prices.to_parquet(root / 'index_daily.parquet', index=False)
    result = comparison(reference, nav, 100)
    assert result['status'] == 'partial' and result['total_return'] is None
    assert result['items'][0]['nav'] is None


def test_no_plan_means_no_implicit_market_reference(data):
    from benchmarks.references import reference_for_run
    with pytest.raises(ContractViolation) as error:
        reference_for_run('BT-test', {})
    assert error.value.code == 'MARKET_REFERENCE_REQUIRED'


def test_display_references_restore_existing_runs_without_rewriting_results(data, monkeypatch):
    from benchmarks import references
    scope = {'market': 'CN-A', 'universe': {'min_listed_days': 120}}
    saved = {'profile': scope, 'config': {'start': '2024-01-02', 'end': '2024-01-03'}}
    before = json.dumps(saved, sort_keys=True)
    calls = []
    def select(scope, start, end, **kwargs):
        calls.append((scope, start, end))
        return MarketReference(code='000985', rationale='覆盖投资范围', selection_timing='after_backtest_display')
    monkeypatch.setattr(references, 'select_display_reference', select)
    rows = [{'backtest_run': f'BT-{i}', 'config': json.dumps(saved), 'metrics': '{"total_return":99}'} for i in range(2)]
    result = references.configure_display_references(rows)
    assert len(result['updated_runs']) == 2 and len(calls) == 1
    assert json.dumps(saved, sort_keys=True) == before
    reference = references.reference_for_run('BT-0', saved)
    assert reference.selection_timing == 'after_backtest_display'
    nav = pd.DataFrame({'date': data[1], 'nav': [110, 121]})
    assert comparison(reference, nav, 100)['status'] == 'available'
    again = references.configure_display_references(rows)
    assert again['already_configured'] == 2 and len(calls) == 1
    saved['config']['end'] = '2024-01-04'
    with pytest.raises(ContractViolation) as error:
        references.reference_for_run('BT-0', saved)
    assert error.value.code == 'MARKET_REFERENCE_CONFIG_CHANGED'


def test_frozen_reference_is_used_without_display_selection(data, monkeypatch):
    from benchmarks.references import reference_for_run
    saved = {'profile': {'extra': {'benchmark_plan': data[2].model_dump()}}}
    reference = reference_for_run('BT-frozen', saved)
    assert reference.code == '000985' and reference.selection_timing == 'before_backtest'


def test_chart_api_returns_current_contract_for_display_reference(data, monkeypatch):
    from webui.backend.routes import backtest
    from benchmarks import references
    saved = {'protocol': {'schema_version': 3}, 'profile': {}, 'config': {'initial_capital': '100'}}
    summary = {'backtest_run': 'BT-api', 'strategy_id': 'test', 'config': json.dumps(saved), 'metrics': '{}', 'status': '{}'}
    nav = [{'date': date, 'nav': nav} for date, nav in zip(data[1].strftime('%Y-%m-%d'), [110, 121])]
    monkeypatch.setattr(backtest.ads, 'query', lambda table, *a, **k: [summary] if table == 'backtest_summary' else nav)
    monkeypatch.setattr(references, 'reference_for_run', lambda *a: MarketReference(code='000985', rationale='股票池', selection_timing='after_backtest_display'))
    result = backtest.get_references('BT-api')
    assert result['status'] == 'available' and result['selection_timing'] == 'after_backtest_display'
    assert set(result['items'][0]) == {'date', 'nav'}
    assert result['excess_return'] == pytest.approx(.11)
    ReferenceComparison.model_validate(result)


def test_monthly_catalogue_and_daily_quote_update_plans(data):
    from providers.markets.cn_a.csmar.incremental import refresh_schedule, _batches_for
    from providers.markets.cn_a.csmar.tables import get_table_config
    assert refresh_schedule('index_basic')['interval_days'] == 30
    daily = refresh_schedule('index_daily')
    assert daily['interval_days'] == 1 and daily['overlap_days'] == 10
    assert daily['deep_interval_days'] == 90 and daily['audit_interval_days'] == 365
    assert _batches_for(get_table_config('index_basic'), '1890-01-01', '2026-11-13') == [('1890-01-01', '2026-11-13')]


def test_removed_market_index_route_is_not_registered():
    from webui.backend.routes.backtest import router
    paths = {route.path for route in router.routes}
    assert '/backtest/runs/{run_id}/references' in paths
    assert '/backtest/runs/{run_id}/market-index' not in paths


def test_sector_region_and_ownership_indices_are_not_whole_market(data):
    root, _, _ = data
    frame = pd.read_parquet(root / 'index_basic.parquet')
    names = ['中证浙江企业综合指数', '上证科创板综合指数', '中证中央企业综合指数', '中证腾讯济安价值100A股指数']
    added = pd.DataFrame([{'index_code': f'9{i:05}', 'name': name, 'asset_type': '1', 'publisher': '3'} for i, name in enumerate(names)])
    pd.concat([frame, added]).to_parquet(root / 'index_basic.parquet', index=False)
    rows = catalogue()
    assert all(not row['market_eligible'] for row in rows if row['name'] in names)
    assert next(row for row in rows if row['index_code'] == '000985')['market_eligible']


def test_nonfinite_quotes_cannot_pass_coverage_validation(data):
    root, dates, plan = data
    frame = pd.read_parquet(root / 'index_daily.parquet')
    frame['close'] = frame['close'].astype(float)
    frame.loc[frame.index_code.eq('000985') & frame.date.eq('2024-01-02'), 'close'] = np.inf
    frame.to_parquet(root / 'index_daily.parquet', index=False)
    with pytest.raises(ContractViolation) as error:
        validate_plan(plan, dates)
    assert error.value.code == 'BENCHMARK_COVERAGE_INCOMPLETE'


def test_selection_uses_available_metadata_and_accepts_weekend_end(data, monkeypatch):
    from benchmarks import selection
    root, _, _ = data
    quotes = pd.read_parquet(root / 'index_daily.parquet')
    quotes.loc[quotes.date.eq('2024-01-03'), 'date'] = '2024-01-05'
    quotes.to_parquet(root / 'index_daily.parquet', index=False)
    seen = {}
    def run_task(self, **kwargs):
        seen.update(json.loads(kwargs['prompt']))
        return {'return_benchmark': {'kind': 'universe_equal_weight'}, 'market_reference': '000985',
                'rationale': '按股票池匹配', 'model_name': 'test'}
    monkeypatch.setattr(selection, 'AIRouter', lambda: SimpleNamespace(run_task=lambda **kwargs: run_task(None, **kwargs)))
    result = selection.select_plan({'universe': {}, 'nav': [1, 9], 'IC': .5, 'holdings': ['a']}, '2024-01-02', '2024-01-06')
    assert result['source'] == 'csmar'
    assert not {'nav', 'IC', 'holdings'} & seen.keys()
    assert any(row['code'] == '000985' for row in seen['index_catalogue'])


def test_frozen_plan_is_reused_without_another_model_request(data, monkeypatch):
    from benchmarks import selection
    class Profile:
        extra = {}
        def model_dump(self, **kwargs):
            return {'extra': self.extra, 'universe': {}}
        def model_copy(self, *, update):
            result = Profile()
            result.extra = update['extra']
            return result
    calls = []
    def choose(*args, **kwargs):
        calls.append(1)
        return {**data[2].model_dump(), 'source': 'csmar', 'version': 1}
    monkeypatch.setattr(selection, 'select_plan', choose)
    first = selection.freeze_profile(Profile(), '2024-01-02', '2024-01-03', key='experiment')
    second = selection.freeze_profile(Profile(), '2024-01-02', '2024-01-03', key='experiment')
    assert len(calls) == 1 and first.extra == second.extra


def test_verified_session_cache_is_private_and_does_not_store_phone(data):
    from paths import workspace
    client = CSMARClient()
    client._token = 'test-token'
    client._cookies = {'D': 'test-cookie'}
    client.save_verified_session()
    path = workspace().experience / '.csmar-session.json'
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(json.loads(path.read_text())) == {'token', 'cookies'}
    path.chmod(0o644)
    with pytest.raises(Exception) as error:
        client.login()
    assert error.value.code == 'PROVIDER_SESSION_PERMISSIONS'


def test_catalogue_refresh_replaces_snapshot_and_removes_obsolete_rows(data):
    from providers.markets.cn_a.csmar.incremental import _merge_to_staged
    root, _, _ = data
    frame = pd.read_parquet(root / 'index_basic.parquet')
    incoming = frame[frame.index_code.eq('000985')].copy()
    added, changed = _merge_to_staged(incoming, 'index_basic')
    assert added == 0 and changed == 2
    assert pd.read_parquet(root / 'index_basic.parquet').index_code.tolist() == ['000985']


def test_scheduler_runs_indices_only_once_per_trading_day(data, monkeypatch):
    from datetime import datetime
    from benchmarks import scheduler
    from providers.markets.cn_a.csmar import incremental, manifest
    from paths import workspace
    (workspace().config).mkdir(parents=True)
    (workspace().config / 'benchmark_data.yaml').write_text("source: csmar\nenabled: true\ntimezone: Asia/Shanghai\ndaily_time: '18:30'\n")
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2026, 11, 16, 18, 35, tzinfo=zone)
    monkeypatch.setattr(scheduler, 'datetime', Clock)
    monkeypatch.setattr(manifest, 'read_manifest', lambda: SimpleNamespace(last_refresh_date_for=lambda *a: ''))
    monkeypatch.setattr(incremental, 'compute_update_window', lambda table: SimpleNamespace(table=table))
    calls = []
    instance = scheduler.BenchmarkDataScheduler(SimpleNamespace(start=lambda *a, **kw: calls.append((a, kw))))
    instance.tick()
    instance.tick()
    assert len(calls) == 1 and calls[0][1]['resource'] == 'data' and calls[0][1]['category'] == 'data_sync'
    from inspect import getclosurevars
    windows = getclosurevars(calls[0][0][1]).nonlocals['windows']
    assert {window.table for window in windows} == {'index_basic', 'index_daily'}
