import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


class ResearchContracts(unittest.TestCase):
    def test_batch_ids_accept_job_names_and_reject_paths(self):
        from quantlens.warehouse import batch_identifier
        self.assertEqual(batch_identifier('verified-backtest_verification-20261113'),'verified-backtest_verification-20261113')
        for value in ['', '../batch', '/batch', 'batch/*', 'a'*129]:
            with self.assertRaises(ValueError):batch_identifier(value)

    def test_all_existing_factor_windows_match_full_panels(self):
        from quantlens.spark_factor_values import run_window,windows
        from factors.registry import FactorRegistry
        rng=np.random.default_rng(42)
        dates=pd.bdate_range('2020-01-01',periods=380)
        codes=[f'{i:06d}' for i in range(200)]
        close=pd.DataFrame(np.exp(np.cumsum(rng.normal(0,.01,(380,200)),axis=0))*20,index=dates,columns=codes)
        panel={name:close.copy() for name in ['adj_close_wd','close','open','high','low','volume','amount','listed_days','is_trading','total_assets','net_profit_ttm_parent','revenue','cfo_net','gross_margin_ttm','consensus_fy1_mean','consensus_fy1_coverage','inst_longline_chg','mktcap_total','ret_wd']}
        panel.update(high=close*1.02,low=close*.98,volume=close*10000,amount=close*1000000,listed_days=close*0+500,is_trading=close.notna(),consensus_fy1_coverage=close*0+5,total_assets=close*100000000,mktcap_total=close*1000000000,ret_wd=close.pct_change())
        panel['industry_code']=pd.DataFrame(np.tile(np.array(['A','B','C','D']*50),(380,1)),index=dates,columns=codes)
        for meta in FactorRegistry().list():
            if meta.name=='model_gbt': continue
            source=(ROOT/meta.plugin_path).read_text();namespace={};exec(source,namespace)
            expected=namespace['compute'](panel).round(6)
            chunks=[]
            for _,history,start,end in windows(dates,meta.min_history,60,200,len(meta.dependencies)):
                idx=dates[(dates>=history)&(dates<=end)]
                frame=pd.DataFrame({'date':np.repeat(idx,200),'code':codes*len(idx)})
                for dep in meta.dependencies: frame[dep]=panel[dep].loc[idx].to_numpy().ravel()
                frame['_output_start']=start;frame['_output_end']=end
                chunks.append(run_window(frame,source,meta.dependencies))
            actual=pd.concat(chunks).pivot(index='date',columns='code',values='value')
            actual.index=pd.to_datetime(actual.index)
            a=actual.reindex(index=expected.index,columns=expected.columns).to_numpy(dtype=float)
            b=expected.to_numpy(dtype=float)
            with self.subTest(factor=meta.name):
                np.testing.assert_allclose(a,b,atol=1e-6,equal_nan=True)

    def test_portfolio_constraints_and_regime(self):
        from portfolio.construct import construct,Constraints
        candidates=pd.DataFrame({'code':[str(i) for i in range(40)],'score':np.arange(40),'cluster_id':[i%4 for i in range(40)],'industry_code':[i%5 for i in range(40)],'is_trading':True,'limit_up':False})
        c=Constraints();normal=construct(candidates,c)
        self.assertLessEqual(normal.weight.sum(),.9+1e-9)
        self.assertLessEqual(normal.weight.max(),.1+1e-9)
        self.assertLessEqual(normal.groupby('cluster_id').weight.sum().max(),.35+1e-9)
        self.assertLessEqual(normal.groupby('industry_code').weight.sum().max(),.30+1e-9)
        self.assertLessEqual(len(normal),30)
        crisis=construct(candidates,c,breadth=.2,regime={'enabled':True})
        self.assertAlmostEqual(crisis.weight.sum(),normal.weight.sum()*.25)
        candidates['is_trading']=False
        self.assertTrue(construct(candidates,c).empty)

    def test_attribution_reconciles_daily_and_compounded(self):
        from quantlens.spark_backtest import attribution
        dates=pd.date_range('2024-01-01',periods=3)
        nav=pd.DataFrame({'date':dates,'daily_return':[.10,-.02,.03]})
        result=attribution(nav,pd.DataFrame(),{(dt,'a'):.05 for dt in dates},{'a':.6})
        np.testing.assert_allclose(result.groupby('date').contribution.sum(),nav.daily_return,atol=1e-6)
        cumulative=result.groupby('factor').tail(1).cumulative_contribution.sum()
        self.assertAlmostEqual(cumulative,float((1+nav.daily_return).prod()-1),places=6)

    def test_attribution_api_keeps_overlapping_runs_separate(self):
        from quantlens.routes import portfolio_attribution
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,QUANTLENS_HOME=temp):
            root=Path(temp)/'data';directory=root/'ads';directory.mkdir(parents=True)
            summaries=pd.DataFrame([
                {'strategy_id':'same','backtest_run':'z-old','status':json.dumps({'updated_at':'2026-11-12T01:00:00Z'})},
                {'strategy_id':'same','backtest_run':'a-new','status':json.dumps({'updated_at':'2026-11-13T01:00:00Z'})},
            ])
            rows=pd.DataFrame([
                {'strategy_id':strategy,'backtest_run':run,'date':'2026-11-11','factor':'f','contribution':value}
                for strategy,run,value in [('same','z-old',.1),('same','a-new',.2),('other','a-new',.9)]
            ])
            tables={}
            for name,frame in [('backtest_summary',summaries),('attribution',rows)]:
                frame.to_parquet(directory/(name+'.parquet'),index=False)
                tables[name]={'files':[{'path':name+'.parquet'}]}
            (root/'ads_manifest.json').write_text(json.dumps({'tables':tables}))
            latest=portfolio_attribution('same')
            self.assertEqual(latest['backtest_run'],'a-new')
            self.assertEqual([r['contribution'] for r in latest['items']],[.2])
            old=portfolio_attribution('same',run_id='z-old')
            self.assertEqual([r['contribution'] for r in old['items']],[.1])

    def test_weights_reject_degenerate_and_normalize_signed(self):
        from quantlens.spark_weight_fit import normalize
        from exceptions import ContractViolation
        np.testing.assert_allclose(normalize([2,-1]),[2/3,-1/3])
        with self.assertRaises(ContractViolation):normalize([0,0])

    def test_factor_ic_and_quintiles_share_average_tie_ranks(self):
        from quantlens.spark_factor_eval import daily_metrics
        frame=pd.DataFrame({'code':list('abcde'),'std_value':[0.,0.,1.,2.,2.],'ret':[1.,1.,2.,3.,3.],
                            'factor':'sample','version':'v1','date':pd.Timestamp('2025-01-02'),'horizon':5})
        result=daily_metrics(frame).iloc[0]
        self.assertAlmostEqual(result.rank_ic,1.)
        self.assertEqual(result._members,['d','e'])
        self.assertAlmostEqual(result.q5_ret,3.)

    def test_factor_applicability_rejects_undeclared_inputs(self):
        from factors.registry import FactorMetadata
        with self.assertRaises(ValueError):FactorMetadata(name='x',dependencies=['close'],eligibility={'unknown':2})
        self.assertEqual(FactorMetadata(name='x',dependencies=['coverage'],eligibility={'coverage':2}).eligibility,{'coverage':2.})

    def test_ads_sync_reuses_verified_batch_and_rejects_tampering(self):
        from quantlens.sync_ads import sync
        from paths import workspace
        import pyarrow as pa,pyarrow.parquet as pq
        from types import SimpleNamespace
        downloads=[]
        def receive(source,destination):
            target=workspace().data/destination.removeprefix('/data/')
            target.mkdir(parents=True)
            pq.write_table(pa.table({'code':['000001'],'value':[1.]}),target/'part-00000.parquet')
            downloads.append(source)
        doc={'run_id':'verified','data_snapshot':'sample','data_as_of':'2025-01-02',
             'tables':{'factor_values':{'source_uri':'hdfs:///published','rows':1,'run_id':'verified'}}}
        hdfs=SimpleNamespace(read_json=lambda uri:json.loads(json.dumps(doc)),get=receive)
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,QUANTLENS_HOME=temp),patch('quantlens.sync_ads.HDFS',return_value=hdfs):
            first=sync('contract-batch');second=sync('contract-batch')
            self.assertEqual(first,second);self.assertEqual(downloads,['hdfs:///published'])
            stored=workspace().data/'ads'/first['tables']['factor_values']['files'][0]['path']
            stored.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'checksum'):sync('contract-batch')

    def test_backtest_reproduces_from_frozen_warehouse_inputs(self):
        from backtest.engine import run_backtest
        from backtest.signal import SignalResult
        from backtest.config import BacktestConfig
        from backtest.protocol import verify_protocol_or_raise
        from strategy import StrategyProfileStore
        from market_rules import MarketRuleRegistry
        from rich.console import Console
        from types import SimpleNamespace
        import io
        profile=StrategyProfileStore().resolve('research_reversal_5')
        # Execution must use the canonical constraints even if an archived
        # presentation field has a conflicting cash percentage.
        from decimal import Decimal
        profile=profile.model_copy(update={'position_constraint':profile.position_constraint.model_copy(update={'min_cash_ratio':Decimal('.95')})})
        protocol=verify_protocol_or_raise();rule=MarketRuleRegistry().get(protocol.market_rule_name)
        dates=pd.bdate_range('2024-01-02',periods=8);codes=[f'{i:06d}' for i in range(30)]
        close=pd.DataFrame(np.tile((20*1.001**np.arange(8))[:,None],(1,30)),index=dates,columns=codes)
        market={'close':close,'high':close*1.01,'low':close*.99,'ret_wd':close*0+.001,'is_trading':close.notna(),'listed_days':close*0+500,'trading_status':close*0+1}
        scores=pd.DataFrame(np.tile(np.arange(30),(8,1)),index=dates,columns=codes)
        payload={'market':market,'signal':SignalResult(scores,{'reversal_5':'fixture'},{'reversal_5':1},provenance={'data_snapshot':'contract'}),
                 'delisting_dates':pd.Series(pd.NaT,index=codes),'mask':close.notna(),
                 'clusters':pd.DataFrame(np.tile(np.arange(30)%5,(8,1)),index=dates,columns=codes),
                 'industries':pd.DataFrame(np.tile(np.arange(30)%4,(8,1)),index=dates,columns=codes),'breadth':pd.Series(.8,index=dates)}
        config=BacktestConfig(profile_name=profile.profile_name,start='2024-01-02',end=str(dates[-1].date()),initial_capital=1000000)
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,QUANTLENS_HOME=temp),patch('backtest.engine.MarketRuleRegistry',return_value=SimpleNamespace(get=lambda name:rule)):
            outcomes=[run_backtest(config,protocol=protocol,profile=profile,warehouse_inputs=payload,run_id=f'BT-CONTRACT-{i}',console=Console(file=io.StringIO())) for i in range(2)]
            self.assertEqual(outcomes[0].metrics,outcomes[1].metrics)
            self.assertGreater(outcomes[0].metrics['trading']['total_buy_amount'],500000)
            self.assertEqual(outcomes[0].metrics['benchmark']['fallback_dates'],['2024-01-02'])
            last_day_mask=payload['mask'].copy();last_day_mask.iloc[-1]=False
            changed=run_backtest(config,protocol=protocol,profile=profile,warehouse_inputs={**payload,'mask':last_day_mask},run_id='BT-CONTRACT-NO-LOOKAHEAD',console=Console(file=io.StringIO()))
            self.assertEqual(outcomes[0].metrics,changed.metrics)

    def test_api_lineage_and_removed_model_routes(self):
        from fastapi.testclient import TestClient
        from webui.backend.server import create_app
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,QUANTLENS_HOME=temp):
            client=TestClient(create_app())
            for path in ['/api/overview','/api/factors','/api/factors/reversal_5/values','/api/factors/reversal_5/evaluation','/api/strategies/x/weights','/api/portfolio/x/candidates','/api/portfolio/x/attribution','/api/settings/data','/api/settings/audit']:
                response=client.get(path)
                self.assertEqual(response.status_code,200,(path,response.text))
                self.assertTrue({'data_as_of','layer','run_id','source_uri'}<=response.json().keys(),path)
            paths=create_app().openapi()['paths']
            for prefix in ['research','rolling','forecasts','tracking','allocation']:
                self.assertFalse(any(path.startswith('/api/'+prefix) or path.startswith('/api/lens/'+prefix) for path in paths))

    def test_protocol_v1_hash_preserved_and_v2_objective(self):
        from backtest.protocol import BacktestProtocol
        from backtest.engine import assess_objective
        old=BacktestProtocol(schema_version=1,benchmark='universe_equal_weight')
        self.assertEqual(old.content_hash(),'af207dd6b1f7aa51')
        p=BacktestProtocol()
        metrics={'returns':{'annualized_return':.15,'total_return':.3},'risk':{'max_drawdown':-.1,'sharpe':1.5},'trading':{'annualized_turnover_oneside':2}}
        self.assertAlmostEqual(assess_objective(metrics,p)['value'],1.5)
        self.assertTrue(assess_objective(metrics,p)['eligible'])
        metrics['trading']['annualized_turnover_oneside']=7
        self.assertFalse(assess_objective(metrics,p)['eligible'])

    def test_ads_manifest_only_reads_published_batch(self):
        from quantlens import ads
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,QUANTLENS_HOME=temp):
            root=Path(temp)/'data/ads/batches/batch1/market';root.mkdir(parents=True)
            pd.DataFrame({'value':[3],'run_id':['batch1']}).to_parquet(root/'part.parquet',index=False)
            (Path(temp)/'data/ads_manifest.json').write_text(json.dumps({'run_id':'batch1','tables':{'market':{'files':[{'path':'batches/batch1/market/part.parquet'}],'source_uri':'hdfs:///quantlens/ads/market','run_id':'batch1'}}}))
            self.assertEqual(ads.query('market')[0]['value'],3)
            pd.DataFrame({'value':[99]}).to_parquet(root/'unpublished.parquet',index=False)
            self.assertEqual(len(ads.query('market')),1)

if __name__=='__main__':unittest.main()
