"""Account permissions must constrain candidates before portfolio selection."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


class AccountPermissions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, QUANTLENS_HOME=self.temp.name)
        self.env.start()
        shutil.copytree(ROOT / 'config', self.root / 'config')
        from quantlens.bootstrap import prepare
        prepare()
        from fastapi.testclient import TestClient
        from webui.backend.server import create_app
        self.client = TestClient(create_app())

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def account(self, alias='账户甲', enabled=False):
        response = self.client.post('/api/accounts', json={
            'alias': alias, 'star_market_enabled': enabled, 'confirm': True})
        self.assertEqual(response.status_code, 200, response.text)
        from portfolio.models import Account
        return Account.model_validate(response.json()['account'])

    def test_create_without_number_preview_and_edit(self):
        preview = self.client.post('/api/accounts', json={'alias': '仅预览'}).json()
        self.assertTrue(preview['requires_confirm'])
        from portfolio.accounts import DEFAULT_ACCOUNT_ID
        self.assertEqual([item['account_id'] for item in self.client.get('/api/accounts').json()['items']], [DEFAULT_ACCOUNT_ID])
        first = self.account()
        second = self.account('账户乙', True)
        self.assertNotEqual(first.account_id, second.account_id)
        self.assertFalse(first.star_market_enabled)
        self.assertTrue(second.star_market_enabled)
        for alias in ['', '   ']:
            self.assertEqual(self.client.post('/api/accounts', json={'alias': alias, 'confirm': True}).status_code, 422)
        response = self.client.post(f'/api/accounts/{first.account_id}/settings', json={
            'alias': '改名账户', 'broker': '券商', 'star_market_enabled': True, 'confirm': True})
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()['account']
        self.assertEqual(updated['account_id'], first.account_id)
        self.assertTrue(updated['star_market_enabled'])
        self.assertEqual(updated['alias'], '改名账户')
        legacy = first.model_dump(exclude={'star_market_enabled'})
        from portfolio.models import Account
        self.assertFalse(Account.model_validate(legacy).star_market_enabled)

    def test_default_research_account_is_registered_once_without_resetting_cash(self):
        from portfolio.accounts import AccountRegistry, DEFAULT_ACCOUNT_ID, DEFAULT_ACCOUNT_ALIAS
        from portfolio.models import Cash
        from quantlens.bootstrap import prepare
        registry = AccountRegistry()
        initial = registry.get(DEFAULT_ACCOUNT_ID)
        self.assertEqual(initial.alias, DEFAULT_ACCOUNT_ALIAS)
        self.assertTrue(initial.star_market_enabled)
        self.assertEqual(initial.reporting_currency, 'CNY')
        self.assertEqual(initial.cash[0].available, Decimal('1000000'))
        self.account('另一个账户')
        cash = Cash(currency='CNY', total=Decimal('12345'), available=Decimal('12345'))
        registry.update(initial.model_copy(update={'cash': [cash]}))
        prepare()
        prepare()
        response = self.client.get('/api/accounts').json()
        self.assertEqual(response['default_account_id'], DEFAULT_ACCOUNT_ID)
        self.assertEqual(response['items'][0]['account_id'], DEFAULT_ACCOUNT_ID)
        self.assertEqual(sum(a['account_id'] == DEFAULT_ACCOUNT_ID for a in response['items']), 1)
        self.assertEqual(registry.get(DEFAULT_ACCOUNT_ID).cash[0].available, Decimal('12345'))

    def test_default_account_restores_interrupted_index_without_resetting_balance(self):
        from portfolio.accounts import AccountRegistry, DEFAULT_ACCOUNT_ID
        registry = AccountRegistry()
        account = registry.get(DEFAULT_ACCOUNT_ID)
        account.cash[0].total = account.cash[0].available = Decimal('76543')
        registry.update(account)
        index = registry._read_index()
        index.pop(DEFAULT_ACCOUNT_ID)
        registry._write_index(index)
        restored = registry.ensure_default()
        self.assertEqual(restored.cash[0].available, Decimal('76543'))
        self.assertEqual(registry.get(DEFAULT_ACCOUNT_ID).cash[0].available, Decimal('76543'))

    def test_permission_and_capital_filter_before_top_n(self):
        from portfolio.construct import construct, Constraints
        frame = pd.DataFrame({'code': ['688001', '689001', '000001', '000002'],
                              'score': [10., 9., 8., 7.], 'close': [10., 10., 1000., 5.]})
        constraints = Constraints(max_positions=1)
        self.assertEqual(construct(frame, constraints).code.tolist(), ['688001'])
        self.assertEqual(construct(frame, constraints, star_market_enabled=False).code.tolist(), ['000001'])
        small = construct(frame, constraints, star_market_enabled=False, capital=10000)
        self.assertEqual(small.code.tolist(), ['000002'])
        self.assertTrue(construct(frame, constraints, star_market_enabled=False, capital=0).empty)

    def test_screen_filters_permissions_before_limit(self):
        restricted = self.account()
        permitted = self.account('账户乙', True)
        directory = self.root / 'data/ads'
        directory.mkdir(parents=True, exist_ok=True)
        day = '2024-01-02'
        frame = pd.DataFrame({'date': [pd.Timestamp(day).date()]*3, 'code': ['688001', '689001', '000001'],
                              'in_universe': True, 'close': 10., 'amount': [9e8, 8e8, 7e8],
                              'momentum_20': 0., 'position_60': .5})
        tables = {}
        for name, values in [('market', pd.DataFrame({'date': [pd.Timestamp(day).date()]})), ('screen', frame)]:
            values.to_parquet(directory / f'{name}.parquet', index=False)
            tables[name] = {'files': [{'path': f'{name}.parquet'}]}
        (self.root / 'data/ads_manifest.json').write_text(json.dumps({'tables': tables}))
        for account, expected in [(restricted, '000001'), (permitted, '688001')]:
            result = self.client.post('/api/screen', json={'as_of': day, 'account_id': account.account_id, 'limit': 1})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['items'][0]['code'], expected)
        from portfolio.accounts import DEFAULT_ACCOUNT_ID
        result = self.client.post('/api/screen', json={'as_of': day, 'limit': 1})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['criteria']['account_id'], DEFAULT_ACCOUNT_ID)
        self.assertEqual(result.json()['items'][0]['code'], '688001')

    def test_plans_use_each_accounts_holdings_cash_and_permissions(self):
        from portfolio.recommend import build_plan
        from portfolio.models import Cash, Action
        from portfolio.store import PortfolioStore
        from strategy import StrategyProfileStore
        restricted = self.account().model_copy(update={'cash': [Cash(currency='CNY', total=Decimal('10000'), available=Decimal('10000'))]})
        permitted = self.account('账户乙', True).model_copy(update={'cash': [Cash(currency='CNY', total=Decimal('100000'), available=Decimal('100000'))]})
        profile = StrategyProfileStore().resolve('research_reversal_5')
        holdings = pd.DataFrame([{'instrument_id': '688001', 'side': 'long', 'qty': '200',
                                 'available_qty': '200', 'cost_price': '9', 'currency': 'CNY', 'as_of': '2024-01-01'}])
        store = PortfolioStore()
        store.read_holdings = lambda account_id, **kwargs: holdings if account_id == restricted.account_id else pd.DataFrame()
        store.read_trades = lambda *args, **kwargs: pd.DataFrame()
        closes = pd.Series({'688001': 10., '000001': 10.})
        calendar = SimpleNamespace(next_trading_day=lambda d: (pd.Timestamp(d)+pd.offsets.BDay()).date())
        with patch('portfolio.recommend.PortfolioStore', return_value=store), patch('portfolio.recommend.latest_close_row', return_value=('2024-01-02', closes)), patch('portfolio.recommend._calendar_for', return_value=calendar), patch('portfolio.recommend._manifest_ref', return_value='fixture'), patch('portfolio.recommend._target_weights', return_value={'688001': Decimal('.1'), '000001': Decimal('.1')}):
            plans = [build_plan(account=account, profile=profile, require_active_profile=False) for account in [restricted, permitted]]
        first, second = plans
        self.assertEqual(first.nav, Decimal('12000'))
        self.assertEqual(second.nav, Decimal('100000'))
        self.assertEqual(first.plan.account_ids, [restricted.account_id])
        self.assertEqual(second.plan.account_ids, [permitted.account_id])
        first_buys = [trade for trade in first.plan.suggested_trades if trade.action in {Action.OPEN, Action.INCREASE}]
        second_buys = [trade for trade in second.plan.suggested_trades if trade.action in {Action.OPEN, Action.INCREASE}]
        self.assertEqual([trade.instrument_id for trade in first_buys], ['000001'])
        self.assertTrue(any(trade.instrument_id == '688001' for trade in second_buys))
        self.assertTrue(any(trade.instrument_id == '688001' and trade.action == Action.CLOSE for trade in first.plan.suggested_trades))
        self.assertLess(first_buys[0].qty, next(trade.qty for trade in second_buys if trade.instrument_id == '000001'))
        # Permissions can change on existing accounts, invalidating saved plans.
        path = store.write_plan(second.plan, primary_account=permitted.account_id)
        response = self.client.post(f'/api/accounts/{permitted.account_id}/settings', json={'alias': permitted.alias, 'star_market_enabled': False, 'confirm': True})
        self.assertEqual(response.status_code, 200, response.text)
        import yaml
        self.assertEqual(yaml.safe_load(path.read_text())['state'], 'stale_for_execution')

    def test_remote_preserves_permission_snapshot(self):
        from backtest.config import BacktestConfig
        from quantlens.remote_backtest import run_remote
        from strategy import StrategyProfileStore
        from backtest.protocol import verify_protocol_or_raise
        config = BacktestConfig(profile_name='research_reversal_5', start='2024-01-02', end='2024-01-05',
                                account_id='account-a', account_name='账户甲', star_market_enabled=False)
        record = SimpleNamespace(run_dir=Path('/unused'), read_metrics=lambda: {})
        with patch('quantlens.remote_backtest.submit') as submit, patch('quantlens.remote_backtest.sync'), patch('quantlens.remote_backtest.BacktestRunStore.open_run', return_value=record):
            run_remote(config, StrategyProfileStore().resolve(config.profile_name), verify_protocol_or_raise())
        arguments = submit.call_args.args
        self.assertEqual(json.loads(arguments[arguments.index('--config')+1]), config.model_dump(mode='json'))

    def test_backtest_uses_account_capital_and_freezes_permission(self):
        account = self.account()
        jobs = []
        def start(name, action, **kwargs):
            jobs.append(action)
            return 'test-task'
        outcome = SimpleNamespace(run_id='BT-test', metrics={})
        with patch('backtest.engine.run_backtest', return_value=outcome) as run, patch('quantlens.routes.manager.start', side_effect=start), patch('portfolio.capital_compare._account_capital', return_value=Decimal('12345')):
            result = self.client.post('/api/backtests', json={'strategy_id': 'research_reversal_5',
                'start': '2024-01-02', 'end': '2024-01-05', 'account_id': account.account_id, 'capital': 999999})
        self.assertEqual(result.status_code, 200, result.text)
        self.client.post(f'/api/accounts/{account.account_id}/settings', json={'alias': account.alias, 'star_market_enabled': True, 'confirm': True})
        jobs[0](lambda message: None)
        config = run.call_args.args[0]
        self.assertEqual(config.initial_capital, Decimal('12345'))
        self.assertFalse(config.star_market_enabled)
        self.assertEqual(config.account_id, account.account_id)

    def test_backtest_without_account_uses_registered_default_and_saves_identity(self):
        from portfolio.accounts import DEFAULT_ACCOUNT_ID, DEFAULT_ACCOUNT_ALIAS
        jobs = []
        def start(name, action, **kwargs):
            jobs.append(action)
            return 'default-test-task'
        outcome = SimpleNamespace(run_id='BT-default', metrics={})
        with patch('backtest.engine.run_backtest', return_value=outcome) as run, \
             patch('quantlens.routes.manager.start', side_effect=start), \
             patch('portfolio.capital_compare._account_capital', return_value=Decimal('1000000')) as capital:
            for account_id in [None, '', DEFAULT_ACCOUNT_ID]:
                payload = {'strategy_id': 'research_reversal_5', 'start': '2024-01-02',
                           'end': '2024-01-05', 'capital': 99}
                if account_id is not None:
                    payload['account_id'] = account_id
                result = self.client.post('/api/backtests', json=payload)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(capital.call_args.args[0].account_id, DEFAULT_ACCOUNT_ID)
                jobs[-1](lambda _: None)
                config = run.call_args.args[0]
                self.assertEqual(config.account_id, DEFAULT_ACCOUNT_ID)
                self.assertEqual(config.account_name, DEFAULT_ACCOUNT_ALIAS)
                self.assertEqual(config.initial_capital, Decimal('1000000'))
                self.assertTrue(config.star_market_enabled)

    def test_real_backtest_has_no_unpermitted_buys(self):
        from backtest.engine import run_backtest
        from backtest.config import BacktestConfig
        from backtest.protocol import verify_protocol_or_raise
        from backtest.signal import SignalResult
        from backtest.report import BacktestRunStore
        from strategy import StrategyProfileStore
        from rich.console import Console
        profile = StrategyProfileStore().resolve('research_reversal_5')
        dates = pd.bdate_range('2024-01-02', periods=4)
        codes = ['688001', '689001', '000001', '000002']
        close = pd.DataFrame(10., index=dates, columns=codes)
        market = {'close': close, 'high': close*1.01, 'low': close*.99, 'ret_wd': close*0,
                  'is_trading': close.notna(), 'listed_days': close*0+500, 'trading_status': close*0+1}
        payload = {'market': market, 'signal': SignalResult(close, {}, {}),
                   'delisting_dates': pd.Series(pd.NaT, index=codes), 'mask': close.notna(),
                   'clusters': close*0, 'industries': close*0, 'breadth': pd.Series(.8, index=dates)}
        for enabled in [True, False]:
            config = BacktestConfig(profile_name=profile.profile_name, start='2024-01-02', end=str(dates[-1].date()), star_market_enabled=enabled)
            outcome = run_backtest(config, profile=profile, protocol=verify_protocol_or_raise(),
                                   warehouse_inputs=payload, console=Console(file=io.StringIO()))
            store = BacktestRunStore.open_run(outcome.run_id)
            trades = store.read_daily('trades')
            bought = trades.loc[(trades.side=='buy') & (trades.status=='filled'), 'instrument'].tolist()
            self.assertTrue(bought)
            self.assertEqual(any(code.startswith(('688', '689')) for code in bought), enabled)
            self.assertEqual(store.read_config()['config']['star_market_enabled'], enabled)


if __name__ == '__main__':
    unittest.main()
