"""Exercise MLlib jobs on a bounded synthetic warehouse, with real publication."""
import os,sys,tempfile,unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))

class SparkAlgorithms(unittest.TestCase):
    def test_model_jobs_share_declared_factors_and_mature_labels(self):
        from quantlens.warehouse import session,make_run,lineage,publish
        from quantlens.spark_kmeans import STYLE_FACTORS
        from factors.registry import FactorRegistry,FactorMetadata
        from pyspark.sql import functions as F
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,QUANTLENS_HOME=temp,QUANTLENS_COMPUTE_LOCAL='1',SPARK_LOCAL_IP='127.0.0.1',PYSPARK_PYTHON=sys.executable):
            registry=FactorRegistry()
            for name in STYLE_FACTORS:registry.upsert(FactorMetadata(name=name,version='v1',direction='positive'))
            registry.upsert(FactorMetadata(name='model_gbt',version='v1',engine='spark',dependencies=STYLE_FACTORS))
            spark=session('algorithm_contracts');spark.sparkContext.setLogLevel('ERROR')
            try:
                context=make_run('algorithm_fixture','synthetic-contract-only')
                random=np.random.default_rng(42);values=[];labels=[];universe=[]
                for year in range(2018,2022):
                    for day in [1,6,11,16,21,26]:
                        dt=date(year,6,day)
                        for stock in range(30):
                            code=f'{stock:06d}';features=random.normal(size=5)
                            for name,value in zip(STYLE_FACTORS,features):values.append((code,dt,float(value),float(value),1.,name,'v1',year,'fixture'))
                            labels.append((code,dt,5,float(.02*features[0]-.01*features[1]+random.normal(0,.001)),date(year,7,1),True,'v1',year))
                            universe.append((code,dt,True,year))
                frame=spark.createDataFrame(values,'code string,date date,value double,std_value double,coverage double,factor string,version string,year int,code_hash string')
                publish(spark,lineage(frame,context),'dws.factor_values',['factor','version','year'],run=context)
                publish(spark,lineage(spark.createDataFrame(labels,'code string,date date,horizon int,ret double,valid_until date,valid boolean,label_version string,year int'),context),'dwd.label_forward',['label_version','horizon','year'],run=context)
                publish(spark,lineage(spark.createDataFrame(universe,'code string,date date,in_universe boolean,year int'),context),'dwd.universe',['year'],run=context)
                from quantlens.spark_weight_fit import exposures,fit
                training=exposures(spark,STYLE_FACTORS).join(spark.table('dwd.label_forward'),['code','date']).where("date<'2021-01-01' AND valid_until<'2021-01-01'")
                for method in ['equal','ic','icir','model_linear','model_logistic']:
                    weights=fit(spark,training,STYLE_FACTORS,method)
                    self.assertAlmostEqual(float(np.abs(weights).sum()),1.,places=8)
                    if method in ['model_linear','model_logistic']:
                        self.assertGreater(weights[0],0);self.assertLess(weights[1],0)
                from quantlens import spark_kmeans,spark_fpgrowth,spark_pca,spark_engine
                spark_kmeans.run(spark,context)
                clusters=spark.table('dws.style_cluster')
                self.assertEqual(clusters.select(F.year('date')).distinct().first()[0],2021)
                self.assertEqual(clusters.where('train_end>=date').count(),0)
                spark_fpgrowth.run(spark,context)
                self.assertGreater(spark.table('dws.factor_itemset').count(),0)
                from quantlens import spark_factor_eval
                spark_factor_eval.run(spark,context,STYLE_FACTORS[0],'2021-01-01','2021-12-31')
                self.assertEqual(spark.table('dws.factor_eval_daily').count(),6)
                evaluation=spark.table('dws.factor_eval').first()
                self.assertEqual(evaluation.horizon,5)
                self.assertAlmostEqual(evaluation.coverage,1.)
                self.assertEqual(spark.table('dws.factor_corr').count(),4)
                spark_pca.run(spark,context,train_end='2020-12-31')
                spark_engine.run(spark,context)
                comparison=spark.table('ads.model_comparison')
                self.assertEqual(comparison.select('experiment','method').distinct().count(),7)
                self.assertGreater(spark.table('dws.factor_values').where("factor='model_gbt'").count(),0)
                self.assertEqual(spark.table('dws.factor_values').where("factor='model_gbt' AND date<'2021-01-01'").count(),0)
            finally:spark.stop()

if __name__=='__main__':unittest.main()
