import os,sys,tempfile,unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

class SparkWarehouse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quantlens.warehouse import session
        cls.temp=tempfile.TemporaryDirectory()
        cls.env=patch.dict(os.environ,QUANTLENS_HOME=cls.temp.name,QUANTLENS_COMPUTE_LOCAL='1',SPARK_LOCAL_IP='127.0.0.1',PYSPARK_PYTHON=sys.executable)
        cls.env.start();cls.spark=session('contracts');cls.spark.sparkContext.setLogLevel('ERROR')

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop();cls.env.stop();cls.temp.cleanup()

    def test_labels_use_global_calendar_and_next_close(self):
        from quantlens.spark_dwd_label import forward_labels
        dates=[date(2024,1,1)+timedelta(days=i) for i in range(6)]
        rows=[(code,dt,float(10+i)) for code in ['a','b'] for i,dt in enumerate(dates) if not(code=='b' and i==2)]
        prices=self.spark.createDataFrame(rows,'code string,date date,adj_close double')
        universe=prices.select('code','date').withColumn('in_universe',__import__('pyspark').sql.functions.lit(True))
        labels=forward_labels(prices,universe,[1])
        a=labels.where("code='a'").orderBy('date').collect()
        self.assertAlmostEqual(a[0].ret,12/11-1)
        self.assertEqual(a[0].valid_until,dates[2]);self.assertFalse(a[-1].valid)
        b=labels.where("code='b' AND date='2024-01-01'").first()
        self.assertFalse(b.valid)  # Missing global exit day must not slide to the next stock observation.

    def test_deduplicate_uses_updated_at(self):
        from quantlens.spark_dwd_prices import deduplicate
        frame=self.spark.createDataFrame([('a','2024-01-01','2024-01-02',1.),('a','2024-01-01','2024-01-03',2.)],['code','date','updated_at','value'])
        self.assertEqual(deduplicate(frame,['code','date']).first().value,2)

    def test_data_boundary_and_suspension_are_not_delisting(self):
        from quantlens.backtest_inputs import confirmed_delisting_dates
        frame=self.spark.createDataFrame([
            ('active',date(2026,11,11),True,False),
            ('suspended',date(2026,11,10),True,False),
            ('suspended',date(2026,11,11),False,False),
            ('delisted',date(2026,10,9),True,True),
            ('delisted',date(2026,10,10),False,True),
        ],'code string,date date,is_trading boolean,is_delisting boolean')
        result={row.code:row.delisting_date for row in confirmed_delisting_dates(frame).collect()}
        self.assertEqual(result,{'active':None,'suspended':None,'delisted':date(2026,10,9)})

    def test_constant_and_empty_paired_correlations_are_null_under_ansi(self):
        from quantlens.spark_weight_fit import safe_correlation
        frame=self.spark.createDataFrame([(1.,1.),(1.,2.),(1.,None)],'x double,y double')
        self.assertIsNone(frame.agg(safe_correlation('x','y').alias('correlation')).first().correlation)
        empty=self.spark.createDataFrame([(None,1.),(None,2.)],'x double,y double')
        self.assertIsNone(empty.agg(safe_correlation('x','y').alias('correlation')).first().correlation)

    def test_universe_excludes_unknown_trading_amount_and_price(self):
        from quantlens.spark_dwd_prices import universe
        rows=[('a',None,10000000.,10.),('b',True,10000000.,10.),('c',True,None,10.),('d',True,10000000.,None)]
        source=self.spark.createDataFrame(rows,'code string,is_trading boolean,amount double,close double')
        from pyspark.sql import functions as F
        source=(source.withColumn('date',F.to_date(F.lit('2024-01-02'))).withColumn('year',F.lit(2024))
                .withColumn('listed_days',F.lit(500)).withColumn('is_st',F.lit(False)).withColumn('is_delisting',F.lit(False)).withColumn('ohlc_valid',F.lit(True)))
        result={row.code:row.in_universe for row in universe(source).collect()}
        self.assertEqual(result,{'a':False,'b':True,'c':False,'d':False})

    def test_ttm_correction_is_visible_only_after_component_publication(self):
        from quantlens.spark_derived import fundamental_ttm
        income=self.spark.createDataFrame([
            ('a','2023-03-31','2023-04-20',20.,100.,50.),
            ('a','2023-12-31','2024-03-20',100.,500.,250.),
            ('a','2023-12-31','2024-05-10',120.,550.,260.),
            ('a','2024-03-31','2024-04-15',30.,150.,60.),
        ],'instrument_id string, report_period string, effective_date string, net_profit_parent double, revenue double, operating_cost double')
        balance=self.spark.createDataFrame([('a','2024-03-31','2024-04-20',200.)],
            'instrument_id string, report_period string, effective_date string, equity_parent double')
        rows=fundamental_ttm(income,balance).where("report_period='2024-03-31'").orderBy('effective_date').collect()
        self.assertEqual([(str(r.effective_date),r.net_profit_ttm_parent) for r in rows],[('2024-04-20',110.),('2024-05-10',130.)])

    def test_consensus_replaces_institution_and_expires_without_future_forecasts(self):
        from quantlens.spark_derived import consensus
        frame=self.spark.createDataFrame([
            ('a','2024-04-01','2024','x','x',10.,'1'),
            ('a','2024-05-05','2024','y','y',30.,'2'),
            ('a','2024-05-10','2024','x','x',20.,'3'),
        ],'instrument_id string, publish_date string, forecast_year string, institution_id string, institution_name string, forecast_net_profit double, report_id string')
        rows={str(row.effective_date):row for row in consensus(frame).collect()}
        self.assertEqual(rows['2024-05-01'].consensus_fy1_mean,10.)
        self.assertEqual(rows['2024-05-05'].consensus_fy1_mean,20.)
        self.assertEqual(rows['2024-05-10'].consensus_fy1_mean,25.)
        self.assertEqual(rows['2024-11-06'].consensus_fy1_coverage,0.)

    def test_pandas_udf_preserves_full_cross_section(self):
        from quantlens.spark_factor_values import run_window
        dates=pd.bdate_range('2024-01-01',periods=10)
        rows=[(0,dt.date(),f'{i:06d}',float(i+j+1),dates[5].date(),dates[-1].date()) for j,dt in enumerate(dates) for i in range(200)]
        frame=self.spark.createDataFrame(rows,'window_id int,date date,code string,adj_close_wd double,_output_start date,_output_end date')
        source=(ROOT/'factors/reversal_5.py').read_text()
        result=frame.groupBy('window_id').applyInPandas(lambda pdf:run_window(pdf,source,['adj_close_wd']),'code string,date date,value double').toPandas()
        self.assertEqual(len(result),1000)
        sample=result[(result.code=='000000')&(pd.to_datetime(result.date)==dates[5])].iloc[0]
        self.assertAlmostEqual(sample.value,round(-(6/1-1),6))

    def test_publish_is_atomic_and_preserves_old_partition(self):
        from quantlens.warehouse import publish,make_run
        frame=self.spark.createDataFrame([('a',1.,2024)],'code string,value double,year int')
        first=publish(self.spark,frame,'dws.contract',['year'],run=make_run('first'))
        old_path=first['partitions'][0]['source_uri']
        with self.assertRaises(Exception):
            publish(self.spark,frame.withColumn('value',__import__('pyspark').sql.functions.lit(9.)),'dws.contract',['year'],run=make_run('blocked'),quality=lambda f:[{'rule':'test'}])
        self.assertEqual(self.spark.table('dws.contract').first().value,1)
        self.assertTrue(Path(old_path).exists())
        publish(self.spark,self.spark.createDataFrame([('a',2.,2024)],frame.schema),'dws.contract',['year'],run=make_run('second'))
        self.assertEqual(self.spark.table('dws.contract').first().value,2)
        self.assertTrue(Path(old_path).exists())

if __name__=='__main__':unittest.main()
