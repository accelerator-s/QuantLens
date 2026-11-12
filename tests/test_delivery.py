import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date,datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


class DeliveryChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'QUANTLENS_HOME':str(self.root)})
        self.env.start()
        shutil.copytree(ROOT/'config/market_rules',self.root/'config/market_rules')
        from quantlens.bootstrap import prepare
        prepare()
        from webui.backend.tasks import manager
        manager._tasks={}
        manager._db_path=self.root/'experience/tasks.sqlite'
        manager._init_db()
        from fastapi.testclient import TestClient
        from webui.backend.server import create_app
        self.client=TestClient(create_app(),raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def account(self,cash='100000'):
        r=self.client.post('/api/accounts',json={'account_id':'qa','alias':'验收账户','confirm':True})
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.post('/api/accounts/qa/cash',json={'total':cash,'confirm':True})
        self.assertEqual(r.status_code,200,r.text)

    def trade(self,**changes):
        payload={'account':'qa','instrument_id':'000001','side':'long','action':'open','qty':'100','price':'10',
                 'fee':'5','executed_at':'2024-01-02T10:00:00+08:00','request_id':'buy1','confirm':True}
        return self.client.post('/api/portfolio/trades/add',json={**payload,**changes})

    def test_cash_holdings_and_idempotency(self):
        self.account()
        first=self.trade();self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(float(first.json()['cash_after']),98995)
        second=self.trade();self.assertEqual(second.status_code,200,second.text)
        self.assertTrue(second.json()['duplicate'])
        sell=self.trade(action='close',price='11',tax='.55',executed_at='2024-01-03T10:00:00+08:00',request_id='sell1')
        self.assertEqual(sell.status_code,200,sell.text)
        self.assertAlmostEqual(float(sell.json()['cash_after']),100089.45)
        self.assertEqual(self.client.get('/api/portfolio/qa/holdings').json()['total_rows'],0)
        self.assertEqual(self.client.get('/api/portfolio/qa/trades').json()['total_rows'],2)

    def test_t_plus_one_and_cash_shortfall(self):
        self.account('1000')
        self.assertEqual(self.trade().status_code,422)
        self.client.post('/api/accounts/qa/cash',json={'total':'100000','confirm':True})
        self.assertEqual(self.trade().status_code,200)
        result=self.trade(action='close',request_id='sell0')
        self.assertNotEqual(result.status_code,200)
        self.assertEqual(self.client.get('/api/portfolio/qa/trades').json()['total_rows'],1)

    def test_trade_failure_rolls_back(self):
        self.account()
        with patch('portfolio.accounts.AccountRegistry.update',side_effect=OSError('写入失败验收')):
            response=self.trade()
        self.assertEqual(response.status_code,500)
        self.assertEqual(self.client.get('/api/portfolio/qa/trades').json()['total_rows'],0)
        self.assertEqual(self.client.get('/api/portfolio/qa/holdings').json()['total_rows'],0)
        self.assertEqual(self.trade().status_code,200)

    def test_direct_factor_execution(self):
        import pandas as pd
        from ai.execution import run_factor
        from exceptions import FactorExecutionError,ContractViolation
        target=self.root/'local-output'
        source=f"from pathlib import Path\ndef compute(panel):\n    Path({str(target)!r}).write_text('ok')\n    return panel['extra'] * 2"
        result=run_factor(source=source,panel_payload={'extra':pd.DataFrame([[2,3]])})
        self.assertEqual(result.iloc[0].tolist(),[4,6]);self.assertEqual(target.read_text(),'ok')
        with self.assertRaises(FactorExecutionError):run_factor(source='def compute(p):\n raise ValueError("失败")',panel_payload={})
        with self.assertRaises(ContractViolation):run_factor(source='def compute(p):\n return 1',panel_payload={})

    def test_shadow_is_not_recent_replay(self):
        from quantlens.model_ops import forecast_mode
        tz=ZoneInfo('Asia/Shanghai')
        self.assertEqual(forecast_mode(date(2026,11,4),datetime(2026,11,4,18,tzinfo=tz)),'shadow')
        self.assertEqual(forecast_mode(date(2026,11,4),datetime(2026,11,5,9,tzinfo=tz)),'shadow')
        self.assertEqual(forecast_mode(date(2026,11,4),datetime(2026,11,5,14,tzinfo=tz)),'historical')
        self.assertEqual(forecast_mode(date(2026,11,4),datetime(2026,11,4,14,tzinfo=tz)),'historical')

    def test_evidence_is_immutable(self):
        from quantlens import store
        record=store.add('check',{'value':1})
        with store.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE records SET body=? WHERE id=?',['{}',record['id']])
        self.assertEqual(store.get(record['id'])['body'],{'value':1})

    def test_rolling_windows_are_disjoint(self):
        from quantlens.rolling import windows,RollingConfig
        folds=windows(RollingConfig(first_test_year=2023,folds=3,train_years=3))
        self.assertEqual(str(folds[0].start),'2019-01-01')
        for a,b in zip(folds,folds[1:]):
            self.assertLess(a.train_end,a.valid_end)
            self.assertEqual(a.end,b.valid_end)

    def test_mutations_reject_foreign_origin(self):
        response=self.client.post('/api/accounts',json={},headers={'Origin':'https://example.org'})
        self.assertEqual(response.status_code,403)

    def test_duplicate_request_rejects_changed_values(self):
        self.account()
        self.assertEqual(self.trade().status_code,200)
        response=self.trade(price='11')
        self.assertEqual(response.json()['detail']['code'],'QL_REQUEST_REUSED')
        self.assertEqual(self.client.get('/api/portfolio/qa/trades').json()['total_rows'],1)

    def test_concurrent_trade_is_applied_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.account()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.trade(),range(4)))
        self.assertTrue(all(r.status_code==200 for r in results))
        self.assertEqual(sum(bool(r.json().get('duplicate')) for r in results),3)
        from portfolio import AccountRegistry
        self.assertEqual(str(AccountRegistry().get('qa').cash[0].total),'98995')

    def test_backups_are_disabled_without_creating_files_or_tasks(self):
        from quantlens.maintenance import backup, restore
        from snapshots import SnapshotStore
        from paths import workspace
        from exceptions import ContractViolation, SnapshotError
        from webui.backend.tasks import manager
        for full in (False, True):
            response = self.client.post('/api/lens/backups', json={'full': full})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['detail']['code'], 'QL_BACKUP_DISABLED')
            with self.assertRaises(ContractViolation):
                backup(None, full=full)
        with self.assertRaises(ContractViolation):
            restore('removed', 'test')
        response = self.client.post('/api/lens/backups/removed/restore', json={'actor': 'test'})
        self.assertEqual(response.json()['detail']['code'], 'QL_BACKUP_DISABLED')
        self.assertFalse((self.root / 'runs/recovery').exists())
        with self.assertRaises(SnapshotError):
            SnapshotStore(workspace()).create('test', ['config/market_rules/CN-A-stock.yaml'])
        self.assertFalse((self.root / 'runs/backups').exists())
        self.assertEqual(list((self.root / '.snapshots').iterdir()), [])
        self.assertEqual(manager._tasks, {})
        self.assertEqual(self.client.get('/api/lens/backups').json(), {'items': [], 'recoveries': []})

    def test_interrupted_trade_recovers(self):
        import json
        from quantlens.bootstrap import prepare
        self.account()
        target=self.root/'portfolio/interrupted.bin'
        target.write_text('before')
        journal=self.root/'portfolio/.transactions/crash'
        journal.mkdir(parents=True)
        (journal/'0.bin').write_text('before')
        (journal/'state.json').write_text(json.dumps({'status':'prepared','files':[{'path':'portfolio/interrupted.bin','existed':True,'copy':'0.bin'}]}))
        target.write_text('partial')
        prepare()
        self.assertEqual(target.read_text(),'before')
        self.assertEqual(json.loads((journal/'state.json').read_text())['status'],'rolled_back')

    def test_reject_foreign_market_and_currency(self):
        self.account()
        response=self.client.post('/api/accounts/qa/cash',json={'currency':'USD','total':'10000','confirm':True})
        self.assertEqual(response.status_code,422)
        response=self.client.post('/api/accounts',json={'account_id':'us','alias':'外部','market':'US','instrument_class':'stock','confirm':True})
        self.assertEqual(response.status_code,422)

    def test_validation_uses_error_contract(self):
        response=self.client.post('/api/lens/rolling',json={'folds':1})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['detail']['code'],'QL_INVALID_INPUT')

    def test_saved_forecast_has_reproducible_factor_values(self):
        import pandas as pd
        from quantlens import store
        from quantlens.model_ops import explain
        model=store.uid('QL');path=store.run_path(model)
        store.write_json(path/'metrics.json',{'importance':[{'feature':'momentum_5','label':'5日动量','importance':.7},
            {'feature':'bias_5','label':'5日均线偏离','importance':.3}]})
        store.write_json(path/'calibration.json',{'absolute_error_q90':.1})
        artifact=self.root/'runs/forecasts/sample';artifact.mkdir(parents=True)
        pd.DataFrame({'instrument_id':['000001','000002','000003'],'prediction':[.01,.02,.03],
            'momentum_5':[1.,2.,3.],'bias_5':[None,2.,3.]}).to_parquet(artifact/'part.parquet',index=False)
        record=store.add('forecast',{'run_id':model,'as_of':'2024-01-02','artifact':'runs/forecasts/sample'})
        result=explain(record['id'],'000001')
        self.assertEqual(result['prediction'],.01)
        self.assertAlmostEqual(result['lower'],-.09)
        self.assertAlmostEqual(result['upper'],.11)
        self.assertEqual(result['features'][0]['percentile'],0)
        self.assertIsNone(result['features'][1]['percentile'])
        self.assertEqual(result['features'][0]['importance'],.7)

    def test_trading_day_uses_beijing_time(self):
        import pandas as pd
        from decimal import Decimal
        from portfolio.adapters.cn_a_stock import CnAStockAdapter
        recent=pd.DataFrame([{'action':'open','qty':'100','executed_at':'2024-01-01T18:00:00+00:00'}])
        available=CnAStockAdapter().compute_available_qty(total_qty=Decimal(100),recent_trades=recent,as_of=date(2024,1,2))
        self.assertEqual(available,0)

    def test_frozen_feature_source_detects_changes(self):
        from quantlens.provenance import capture,feature_builder
        from quantlens import store
        from exceptions import ContractViolation
        root=store.run_path(store.uid('QL'));root.mkdir(parents=True)
        capture(root)
        (root/'source/spark_features.py').write_text('def build():return None')
        with self.assertRaises(ContractViolation) as error:feature_builder(root)
        self.assertEqual(error.exception.code,'QL_SOURCE_CHANGED')

    def test_stalled_download_is_reported_without_resubmission(self):
        import io
        from rich.console import Console
        from providers.exceptions import ProviderDataError
        from providers.markets.cn_a.csmar.incremental import _download_batch_with_retry
        error=ProviderDataError(code='PROVIDER_PACK_STUCK',message='数据打包停滞')
        with patch('providers.markets.cn_a.csmar.incremental.download_table_full',side_effect=error) as download:
            with self.assertRaises(ProviderDataError):
                _download_batch_with_retry('prices_daily',None,'2026-11-05','2026-11-05',
                    instrument_codes=None,batch_index=1,batch_total=1,remaining_batches=[],console=Console(file=io.StringIO()))
        self.assertEqual(download.call_count,1)


if __name__=='__main__':unittest.main()
