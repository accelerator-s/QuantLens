"""Snapshot names and calendar markers use published data, independent of pagination."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webui.backend.routes import backtest

    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    monkeypatch.setattr(backtest, '_require_valid_run', lambda _: {
        'config': {'config': {'start': '2026-10-29', 'end': '2026-11-07'}}})
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/stock_names.json').write_text(json.dumps({'names': {
        '000001': '平安银行', '000022': '深赤湾A',
    }}), encoding='utf-8')
    root = tmp_path / 'data/ads'
    root.mkdir(parents=True)
    manifest = {'tables': {}}

    def publish(table, rows):
        pd.DataFrame(rows).to_parquet(root / f'{table}.parquet', index=False)
        manifest['tables'][table] = {'files': [{'path': f'{table}.parquet'}]}
        (tmp_path / 'data/ads_manifest.json').write_text(json.dumps(manifest))

    publish('backtest_nav', [{'backtest_run': 'run', 'date': date} for date in
                            ['2026-10-30', '2026-11-02', '2026-11-03', '2026-11-04']])
    publish('backtest_positions', [
        {'backtest_run': 'run', 'date': day, 'instrument': code,
         'weight': weight, 'market_value': 10000, 'shares_nominal': 100}
        for day in ['2026-10-30', '2026-11-04']
        for code, weight in [('000001', .4), ('000022', .3), ('600000', .2), ('999999', .1)]
    ])
    publish('stock_prices', [{'code': '000001', 'name': None}, {'code': '600000', 'name': '浦发银行'}])
    publish('backtest_trades', [
        {'backtest_run': 'run', 'date': '2026-10-30', 'status': 'filled', 'shares': 100}
        for _ in range(10001)
    ] + [
        {'backtest_run': 'run', 'date': '2026-11-02', 'status': 'rejected_limit_up', 'shares': 100},
        {'backtest_run': 'run', 'date': '2026-11-02', 'status': 'pending_limit_down', 'shares': 100},
        {'backtest_run': 'run', 'date': '2026-11-02', 'status': 'filled', 'shares': 0},
        {'backtest_run': 'other', 'date': '2026-11-02', 'status': 'filled', 'shares': 100},
        {'backtest_run': 'run', 'date': '2026-11-03', 'status': 'forced_delist_close', 'shares': 100},
        {'backtest_run': 'run', 'date': '2026-11-04', 'status': 'filled', 'shares': 100},
    ])
    app = FastAPI()
    app.include_router(backtest.router, prefix='/api')
    with TestClient(app) as client:
        yield client, manifest, tmp_path


def test_snapshot_names_and_adjustment_dates_across_pages(snapshots):
    client, _, _ = snapshots
    url = '/api/backtest/runs/run/positions'
    first = client.get(url, params={'limit': 2}).json()
    assert first['date'] == '2026-11-04'
    assert first['snapshot_date'] == first['date']
    assert first['date_range'] == {'start': '2026-10-29', 'end': '2026-11-07'}
    assert first['total_rows'] == 4
    assert [(row['instrument'], row['stock_name']) for row in first['items']] == [
        ('000001', '平安银行'), ('000022', '深赤湾A')]
    assert first['adjustment_dates'] == ['2026-10-30', '2026-11-03', '2026-11-04']
    last = client.get(url, params={'date': first['date'], 'limit': 2, 'offset': 2}).json()
    assert [(row['instrument'], row['stock_name']) for row in last['items']] == [
        ('600000', '浦发银行'), ('999999', '')]
    assert last['adjustment_dates'] == first['adjustment_dates']
    empty = client.get(url, params={'date': '2026-11-02'}).json()
    assert empty['items'] == [] and empty['total_rows'] == 0
    assert empty['dates'] == first['dates']


def test_snapshot_without_trades_still_has_dates_and_names(snapshots):
    client, manifest, root = snapshots
    del manifest['tables']['backtest_trades']
    (root / 'data/ads_manifest.json').write_text(json.dumps(manifest))
    result = client.get('/api/backtest/runs/run/positions').json()
    assert result['adjustment_dates'] == []
    assert result['dates'] and result['items'][0]['stock_name'] == '平安银行'


def test_non_trading_dates_use_the_previous_close_but_preserve_selected_date(snapshots):
    client, _, _ = snapshots
    url = '/api/backtest/runs/run/positions'
    for day, close in [('2026-10-31', '2026-10-30'), ('2026-11-01', '2026-10-30'),
                       ('2026-11-07', '2026-11-04')]:
        result = client.get(url, params={'date': day, 'limit': 2, 'offset': 2}).json()
        assert result['date'] == day and result['snapshot_date'] == close
        assert result['total_rows'] == 4
        assert [(row['instrument'], row['date']) for row in result['items']] == [
            ('600000', close), ('999999', close)]
    # Before the first close, and after a close with no holdings, stay empty.
    for day in ['2026-10-29', '2026-11-02']:
        result = client.get(url, params={'date': day}).json()
        assert result['date'] == day and result['items'] == []
    assert client.get(url, params={'date': '2026-10-28'}).status_code == 400
    assert client.get(url, params={'date': '2026-11-08'}).status_code == 400


def test_missing_config_uses_the_published_date_range(snapshots, monkeypatch):
    from webui.backend.routes import backtest
    client, _, _ = snapshots
    monkeypatch.setattr(backtest, '_require_valid_run', lambda _: {})
    result = client.get('/api/backtest/runs/run/positions').json()
    assert result['date_range'] == {'start': '2026-10-30', 'end': '2026-11-04'}
