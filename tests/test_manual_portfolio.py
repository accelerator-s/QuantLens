"""Manual entries, T+1 availability and auditable trade reversal must agree."""
import os
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


class ManualPortfolio(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, QUANTLENS_HOME=self.temp.name)
        self.env.start()
        shutil.copytree(ROOT / 'config/market_rules', self.root / 'config/market_rules')
        from quantlens.bootstrap import prepare
        prepare()
        from fastapi.testclient import TestClient
        from webui.backend.server import create_app
        self.client = TestClient(create_app(), raise_server_exceptions=False)
        self.assertEqual(self.client.post('/api/accounts', json={'account_id': 'manual', 'alias': '手动账户', 'confirm': True}).status_code, 200)
        self.assertEqual(self.client.post('/api/accounts/manual/cash', json={'total': '100000', 'confirm': True}).status_code, 200)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def trade(self, **changes):
        return self.client.post('/api/portfolio/trades/add', json={
            'account': 'manual', 'instrument_id': '000001', 'action': 'buy', 'qty': '100', 'price': '10',
            'request_id': 'buy-one', 'executed_at': '2024-01-02T10:00:00+08:00',
            'confirm': True, **changes})

    def holding(self, **changes):
        return self.client.put('/api/portfolio/manual/holdings', json={
            'instrument_id': '000001', 'qty': '1000', 'cost_price': '8', 'confirm': True, **changes})

    def undo(self, trade_id, confirm=True):
        return self.client.post('/api/portfolio/manual/trades/undo', json={'trade_id': trade_id, 'confirm': confirm})

    def balance(self):
        from portfolio import AccountRegistry
        return AccountRegistry().get('manual').cash[0].total

    def history(self, **params):
        return self.client.get('/api/portfolio/manual/trades', params=params).json()

    def positions(self, day=None):
        if day:
            now = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
            with patch('webui.backend.routes.portfolio.datetime', SimpleNamespace(now=lambda tz: now)):
                return self.client.get('/api/portfolio/manual/holdings').json()['items']
        return self.client.get('/api/portfolio/manual/holdings').json()['items']

    def test_buy_freezes_until_next_trading_day_and_undo_refunds_fees(self):
        result = self.trade()
        self.assertEqual(result.status_code, 200, result.text)
        trade_id = result.json()['trade_id']
        self.assertEqual(self.balance(), Decimal('98994.99'))
        frozen, = self.positions('2024-01-02T15:00:00')
        self.assertEqual((frozen['qty'], frozen['available_qty'], frozen['frozen_qty']), ('100', '0', '100'))
        thawed, = self.positions('2024-01-03T09:00:00')
        self.assertEqual((thawed['available_qty'], thawed['frozen_qty']), ('100', '0'))
        preview = self.undo(trade_id, False)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()['requires_confirm'])
        self.assertEqual(self.balance(), Decimal('98994.99'))
        undone = self.undo(trade_id)
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(self.balance(), Decimal('100000'))
        self.assertEqual(self.positions(), [])
        record, = self.history()['items']
        self.assertEqual(Decimal(record['fee']), Decimal('5.01'))
        self.assertEqual(Decimal(record['tax']), Decimal('0'))
        self.assertTrue(record['is_reversed'])
        self.assertTrue(record['reversed_at'])
        self.assertEqual(record['trade_id'], trade_id)
        self.assertEqual(self.history()['latest_trade_id'], '')
        from portfolio import PortfolioStore
        store = PortfolioStore()
        self.assertTrue(store.read_trades('manual', market='CN-A', instrument_class='stock').empty)
        self.assertEqual(len(store.read_trades('manual', market='CN-A', instrument_class='stock', include_reversed=True)), 1)
        self.assertTrue(self.undo(trade_id).json()['duplicate'])
        self.assertTrue(self.trade().json()['reversed'])
        self.assertEqual(self.balance(), Decimal('100000'))
        self.assertEqual(self.history()['total_rows'], 1)

    def test_manual_holding_and_sell_reversal_preserve_cost_and_cash(self):
        preview = self.holding(confirm=False)
        self.assertTrue(preview.json()['requires_confirm'])
        self.assertEqual(self.positions(), [])
        self.assertEqual(self.holding().status_code, 200)
        self.assertEqual(self.balance(), Decimal('100000'))
        result = self.trade(action='sell', price='12', request_id='sell-one')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.balance(), Decimal('101194.39'))
        self.assertEqual(self.positions()[0]['qty'], '900')
        undone = self.undo(result.json()['trade_id'])
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(self.balance(), Decimal('100000'))
        position, = self.positions()
        self.assertEqual((position['qty'], position['cost_price']), ('1000', '8'))

    def test_undo_stack_keeps_every_transaction_and_restores_weighted_cost(self):
        first = self.trade().json()['trade_id']
        second_result = self.trade(price='20', request_id='buy-two', executed_at='2024-01-03T10:00:00+08:00')
        self.assertEqual(second_result.status_code, 200, second_result.text)
        second = second_result.json()['trade_id']
        third_result = self.trade(action='sell', price='18', request_id='sell-three', executed_at='2024-01-04T10:00:00+08:00')
        self.assertEqual(third_result.status_code, 200, third_result.text)
        third = third_result.json()['trade_id']
        self.assertEqual(self.undo(first).status_code, 422)
        self.assertEqual(self.undo(third).status_code, 200)
        position, = self.positions()
        self.assertEqual(Decimal(position['cost_price']), Decimal('15'))
        self.assertEqual(position['qty'], '200')
        self.assertEqual(self.balance(), Decimal('96989.97'))
        self.assertEqual(self.undo(second).status_code, 200)
        self.assertEqual(Decimal(self.positions()[0]['cost_price']), Decimal('10'))
        self.assertEqual(self.balance(), Decimal('98994.99'))
        self.assertEqual(self.undo(first).status_code, 200)
        self.assertEqual(self.balance(), Decimal('100000'))
        self.assertEqual(self.positions(), [])
        self.assertEqual(self.history()['total_rows'], 3)
        self.assertTrue(all(row['is_reversed'] for row in self.history()['items']))
        self.assertEqual(len(self.history(limit=1, offset=1)['items']), 1)

    def test_t_plus_one_rejects_selling_frozen_stock(self):
        self.assertEqual(self.trade().status_code, 200)
        result = self.trade(action='sell', request_id='same-day-sell')
        self.assertNotEqual(result.status_code, 200)
        self.assertEqual(self.balance(), Decimal('98994.99'))
        self.assertEqual(self.history()['total_rows'], 1)

    def test_weekend_does_not_release_friday_purchase(self):
        result = self.trade(executed_at='2024-01-05T10:00:00+08:00')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.positions('2024-01-06T12:00:00')[0]['frozen_qty'], '100')
        self.assertEqual(self.positions('2024-01-08T09:00:00')[0]['frozen_qty'], '0')

    def test_entries_and_reversals_invalidate_previous_suggestions(self):
        from datetime import UTC
        from portfolio import Plan, PortfolioStore
        import yaml
        def plan(suffix):
            value = Plan(plan_id=f'PLN-20240102-{suffix}', account_ids=['manual'],
                         generated_at=datetime.now(UTC), effective_window={'start': datetime.now(UTC), 'end': datetime.now(UTC)},
                         strategy_profile_ref='fixture')
            return PortfolioStore().write_plan(value, primary_account='manual')
        first = plan('aaaaaa')
        self.assertEqual(self.holding().status_code, 200)
        self.assertEqual(yaml.safe_load(first.read_text())['state'], 'stale_for_execution')
        second = plan('bbbbbb')
        trade = self.trade()
        self.assertEqual(trade.status_code, 200, trade.text)
        self.assertEqual(yaml.safe_load(second.read_text())['state'], 'stale_for_execution')
        third = plan('cccccc')
        self.assertEqual(self.undo(trade.json()['trade_id']).status_code, 200)
        self.assertEqual(yaml.safe_load(third.read_text())['state'], 'stale_for_execution')
        fourth = plan('dddddd')
        self.assertEqual(self.client.post('/api/accounts/manual/cash', json={'total': '200000', 'confirm': True}).status_code, 200)
        self.assertEqual(yaml.safe_load(fourth.read_text())['state'], 'stale_for_execution')

    def test_undo_does_not_overwrite_other_manual_holdings_or_cash_adjustment(self):
        trade_id = self.trade().json()['trade_id']
        self.assertEqual(self.holding(instrument_id='600000').status_code, 200)
        self.assertEqual(self.client.post('/api/accounts/manual/cash', json={'total': '50000', 'confirm': True}).status_code, 200)
        result = self.undo(trade_id)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.balance(), Decimal('51005.01'))
        position, = self.positions()
        self.assertEqual(position['instrument_id'], '600000')

    def test_undo_rejects_changed_position_and_insufficient_sell_proceeds(self):
        trade_id = self.trade().json()['trade_id']
        self.assertEqual(self.holding(qty='500').status_code, 200)
        self.assertEqual(self.undo(trade_id).status_code, 422)
        self.assertFalse(self.history()['items'][0]['is_reversed'])
        sold = self.trade(action='sell', qty='100', request_id='sell-after-manual', executed_at='2024-01-03T10:00:00+08:00')
        self.assertEqual(sold.status_code, 200, sold.text)
        self.client.post('/api/accounts/manual/cash', json={'total': '0', 'confirm': True})
        self.assertEqual(self.undo(sold.json()['trade_id']).status_code, 422)
        self.assertEqual(self.balance(), 0)

    def test_undo_is_atomic_and_concurrent_retries_refund_once(self):
        trade_id = self.trade().json()['trade_id']
        with patch('portfolio.accounts.AccountRegistry.update', side_effect=OSError('write failed')):
            self.assertEqual(self.undo(trade_id).status_code, 500)
        self.assertEqual(self.balance(), Decimal('98994.99'))
        self.assertEqual(self.positions()[0]['qty'], '100')
        self.assertFalse(self.history()['items'][0]['is_reversed'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.undo(trade_id), range(4)))
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(sum(bool(result.json().get('duplicate')) for result in results), 3)
        self.assertEqual(self.balance(), Decimal('100000'))

    def test_fixed_long_side_validation_and_automatic_buy_action(self):
        self.assertEqual(self.trade(side='short').status_code, 422)
        self.assertEqual(self.trade(currency='USD').status_code, 422)
        self.assertEqual(self.trade(qty='0.5').status_code, 422)
        self.assertEqual(self.trade(instrument_id='not-a-code').status_code, 422)
        self.assertEqual(self.trade(instrument_id='688001', qty='200').status_code, 422)
        self.assertEqual(self.holding(qty='1.5').status_code, 422)
        self.assertEqual(self.trade().status_code, 200)
        self.assertTrue(self.trade().json()['duplicate'])
        second = self.trade(request_id='auto-increase')
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self.positions()[0]['qty'], '200')
        self.assertEqual(self.holding(qty='0', cost_price='0').status_code, 200)
        self.assertEqual(self.positions(), [])
        self.assertEqual(self.history()['total_rows'], 2)

    def test_fees_and_stamp_duty_are_computed_by_trade_rules(self):
        from market_rules import MarketRuleRegistry, RuleEngine
        engine = RuleEngine(MarketRuleRegistry().get('CN-A-stock'))
        result = self.trade(qty='1000', price='10')
        self.assertEqual(result.status_code, 200, result.text)
        bought = self.history()['items'][0]
        self.assertEqual(Decimal(bought['fee']), engine.calculate_fees(Decimal('10000'), 'buy').total)
        self.assertEqual(Decimal(bought['tax']), 0)
        sold = self.trade(action='sell', qty='1000', price='12', request_id='sell-auto', executed_at='2024-01-03T10:00:00+08:00')
        self.assertEqual(sold.status_code, 200, sold.text)
        record = self.history()['items'][0]
        self.assertEqual(Decimal(record['fee']), engine.calculate_fees(Decimal('12000'), 'sell').total)
        self.assertEqual(Decimal(record['tax']), engine.calculate_taxes(Decimal('12000'), 'sell').total)
        self.assertEqual(self.trade(fee='0').status_code, 422)

    def test_removed_portfolio_routes_are_absent(self):
        from webui.backend.server import create_app
        routes = create_app().openapi()['paths']
        for route in ['/api/portfolio/snapshot/submit', '/api/portfolio/snapshot/commit',
                      '/api/portfolio/trades/append', '/api/portfolio/trades/fix',
                      '/api/portfolio/{account_id}/reconcile', '/api/portfolio/plan/submit',
                      '/api/portfolio/plan/{plan_id}/transition', '/api/portfolio/plan/{plan_id}/review',
                      '/api/portfolio/plan/{plan_id}/capital-comparison', '/api/portfolio/{account_id}/snapshots']:
            self.assertNotIn(route, routes)

    def test_stock_suggestions_match_prefix_name_and_refresh_published_index(self):
        import json
        import pandas as pd
        directory = self.root / 'data/ads'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'stocks.parquet'
        pd.DataFrame({'code': ['000001', '000001', '000002', '600000'],
                      'name': ['平安银行', '平安银行', '万科A', '浦发银行']}).to_parquet(path, index=False)
        (self.root / 'data/ads_manifest.json').write_text(json.dumps({
            'tables': {'stock_prices': {'files': [{'path': 'stocks.parquet'}]}}}))
        for prefix in ['0', '00', '000', '0000', '00000']:
            result = self.client.get('/api/stocks/suggestions', params={'q': prefix})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([row['code'] for row in result.json()['items']], ['000001', '000002'])
        exact = self.client.get('/api/stocks/suggestions', params={'q': '000001'}).json()['items']
        self.assertEqual(exact, [{'code': '000001', 'name': '平安银行'}])
        named = self.client.get('/api/stocks/suggestions', params={'q': '浦发'}).json()['items']
        self.assertEqual(named, [{'code': '600000', 'name': '浦发银行'}])
        self.assertEqual(len(self.client.get('/api/stocks/suggestions', params={'limit': 1}).json()['items']), 1)
        pd.DataFrame({'code': ['000003'], 'name': ['更新后的股票']}).to_parquet(path, index=False)
        updated = self.client.get('/api/stocks/suggestions', params={'q': '000'}).json()['items']
        self.assertEqual(updated, [{'code': '000003', 'name': '更新后的股票'}])

    def test_stock_suggestions_empty_and_invalid_queries(self):
        response = self.client.get('/api/stocks/suggestions', params={'q': "' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'], [])
        self.assertEqual(self.client.get('/api/stocks/suggestions', params={'q': 'x' * 33}).status_code, 422)
        self.assertEqual(self.client.get('/api/stocks/suggestions', params={'limit': 0}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
