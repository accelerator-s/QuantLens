"""Run the frozen matching contract twice against actual warehouse inputs."""
import json
import pandas as pd
from pyspark.sql import functions as F
from quantlens.warehouse import job
from quantlens.spark_backtest import run
from backtest.report import BacktestRunStore
from strategy import StrategyProfileStore
from quantlens.backtest_inputs import inputs
from backtest.engine import run_backtest
from backtest.config import BacktestConfig
from backtest.protocol import verify_protocol_or_raise

with job('backtest_verification') as (spark,context):
    from quantlens.sync_factor_meta import run as sync_metadata
    sync_metadata(spark,context)
    strategy='research_reversal_5';start='2026-10-01';end='2026-11-11'
    first=run(spark,context,strategy,start,end,run_id='BT-REAL-'+context['run_id'])
    protocol=verify_protocol_or_raise();profile=StrategyProfileStore().resolve(strategy)
    payload=inputs(profile,start,end,spark)
    second=run_backtest(BacktestConfig(profile_name=strategy,start=start,end=end),protocol=protocol,profile=profile,warehouse_inputs=payload,run_id='BT-REPEAT-'+context['run_id'])
    assert first.metrics==second.metrics,(first.metrics,second.metrics)
    attr=spark.table('ads.attribution').where(F.col('backtest_run')==first.run_id).toPandas()
    daily_error=float((attr.groupby('date').contribution.sum()-attr.groupby('date').portfolio_return.first()).abs().max())
    nav=BacktestRunStore.open_run(first.run_id).read_daily('nav')
    last=attr.sort_values('date').groupby('factor').tail(1).cumulative_contribution.sum()
    cumulative_error=abs(float(last)-float((1+nav.daily_return).prod()-1))
    assert daily_error<=1e-6 and cumulative_error<=1e-6
    print('QUANTLENS_BACKTEST_VERIFICATION='+json.dumps({'run_id':first.run_id,'repeat_run_id':second.run_id,'identical_metrics':True,'daily_attribution_error':daily_error,'cumulative_attribution_error':cumulative_error,'metrics':first.metrics,'data_snapshot':context['data_snapshot']},default=str),flush=True)
    # Independently recompute thirty published daily Rank IC observations.
    import numpy as np
    from factors.registry import FactorRegistry
    version=FactorRegistry().get('reversal_5').version
    expected=(spark.table('dws.factor_eval_daily').where((F.col('factor')=='reversal_5')&(F.col('version')==version)&(F.col('horizon')==5))
              .orderBy(F.col('date').desc()).limit(30).select('date','rank_ic').toPandas())
    expected.date=pd.to_datetime(expected.date)
    low=str(expected.date.min())[:10];high=str(expected.date.max())[:10]
    values=spark.table('dws.factor_values').where((F.col('factor')=='reversal_5')&(F.col('version')==version)&(F.col('date')>=low)&(F.col('date')<=high)).select('code','date','std_value')
    labels=spark.table('dwd.label_forward').where((F.col('horizon')==5)&(F.col('label_version')=='v1')&F.col('valid')&(F.col('date')>=low)&(F.col('date')<=high)).select('code','date','ret')
    universe=spark.table('dwd.universe').where('in_universe').select('code','date')
    sample=values.join(labels,['code','date']).join(universe,['code','date']).toPandas();sample.date=pd.to_datetime(sample.date)
    checks=[]
    for dt,group in sample.groupby('date'):
        valid=group[['std_value','ret']].replace([np.inf,-np.inf],np.nan).dropna()
        actual=float(valid.rank(method='average').corr().iloc[0,1])
        stored=float(expected.loc[expected.date==dt,'rank_ic'].iloc[0])
        checks.append({'date':str(dt.date()),'pandas_ic':actual,'spark_ic':stored,'absolute_error':abs(actual-stored),'n':len(valid)})
    max_error=max(item['absolute_error'] for item in checks)
    assert max_error<=.002,max_error
    print('QUANTLENS_IC_CONSISTENCY='+json.dumps({'factor':'reversal_5','version':version,'max_absolute_error':max_error,'days':checks,**context},default=str),flush=True)
    styles=spark.table('dws.style_cluster')
    assert not styles.where((F.col('train_end')>=F.col('date'))|~F.col('k').between(5,8)|(F.col('seed')!=42)).take(1)
    clusters=[row.asDict() for row in styles.select('year','k','seed','train_start','train_end','silhouette').distinct().orderBy('year').collect()]
    pca=spark.table('dws.pca_loadings')
    pca_date=pca.agg(F.max('as_of').alias('date')).first().date
    components=pca.where(F.date_format('as_of','yyyy-MM-dd')==str(pca_date)).select('component','explained_variance','k','train_end').distinct().orderBy('component').collect()
    explained=sum(row.explained_variance for row in components)
    assert explained>=.8 and components
    itemsets=spark.table('dws.factor_itemset')
    assert not itemsets.where((F.col('support')<.05)|((F.col('kind')=='rule')&(F.col('confidence')<.6))|F.col('interpretation').isNull()).take(1)
    itemset_counts=[row.asDict() for row in itemsets.groupBy('kind').count().collect()]
    print('QUANTLENS_ALGORITHMS='+json.dumps({'clusters':clusters,'pca_components':[row.asDict() for row in components],'pca_explained_variance':explained,'itemsets':itemset_counts,**context},default=str),flush=True)
    from quantlens.spark_ads import export
    export(spark,context,'verified-backtest-'+context['run_id'])
