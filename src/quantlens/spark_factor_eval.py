from __future__ import annotations
import numpy as np
import pandas as pd
from quantlens.warehouse import job, lineage, publish


def daily_metrics(pdf):
    pdf = pdf.sort_values('code')
    valid = pdf[['std_value','ret']].replace([np.inf,-np.inf],np.nan).dropna()
    ic = valid.std_value.rank().corr(valid.ret.rank()) if len(valid)>2 else np.nan
    if len(valid):
        groups = np.minimum(4,(valid.std_value.rank(method='average',pct=True)*5-1e-9).astype(int))
        returns = valid.ret.groupby(groups).mean()
        monotonicity = returns.index.to_series().corr(pd.Series(returns.values,index=returns.index),method='pearson') if len(returns)>1 else np.nan
        grouped = [float(returns.get(i,np.nan)) for i in range(5)]
        members=pdf.loc[valid.index[groups==4],'code'].astype(str).tolist()
    else: grouped,monotonicity,members = [np.nan]*5,np.nan,[]
    row = pdf.iloc[0]
    return pd.DataFrame([{'factor':row.factor,'version':row.version,'date':row.date,'horizon':int(row.horizon),
                          'n':len(valid),'rank_ic':ic,'coverage':len(valid)/len(pdf) if len(pdf) else 0,
                          **{f'q{i+1}_ret':grouped[i] for i in range(5)},'monotonicity':monotonicity,'_members':members}])


def evaluate_one(spark, context, name, version, start=None, end=None):
    from pyspark.sql import functions as F, Window
    values = spark.table('dws.factor_values').select('code','date','factor','version','std_value')
    values=values.where((F.col('factor')==name)&(F.col('version')==version))
    if start: values=values.where(F.col('date')>=start)
    if end: values=values.where(F.col('date')<=end)
    evaluation_start=start or values.agg(F.min('date').alias('date')).first().date
    evaluation_end=end or values.agg(F.max('date').alias('date')).first().date
    labels = spark.table('dwd.label_forward').where("label_version='v1' AND valid").where((F.col('date')>=F.lit(evaluation_start))&(F.col('date')<=F.lit(evaluation_end)))
    if end:labels=labels.where(F.col('valid_until')<=end)
    labels=labels.select('code','date','horizon','ret')
    universe = spark.table('dwd.universe').where('in_universe').where((F.col('date')>=F.lit(evaluation_start))&(F.col('date')<=F.lit(evaluation_end))).select('code','date')
    denominator=universe.groupBy('date').agg(F.count('*').alias('_universe_n'))
    joined = values.join(universe,['code','date']).join(labels,['code','date'])
    schema = 'factor string, version string, date date, horizon int, n long, rank_ic double, coverage double, q1_ret double, q2_ret double, q3_ret double, q4_ret double, q5_ret double, monotonicity double, _members array<string>'
    daily = joined.groupBy('factor','version','date','horizon').applyInPandas(daily_metrics,schema)
    daily=daily.join(denominator,'date').withColumn('coverage',F.col('n')/F.col('_universe_n')).drop('_universe_n')
    # A factor's declared applicability also defines its evaluation population.
    # Structural absence of analyst coverage is not a missing input observation.
    import json
    from quantlens.panel_source import fields
    evaluated={(row.factor,row.version) for row in values.select('factor','version').distinct().collect()}
    if spark.catalog.tableExists('dws.factor_meta'):
        for record in spark.table('dws.factor_meta').select('name','version','metadata').collect():
            if (record.name,record.version) not in evaluated:continue
            meta=json.loads(record.metadata);filters=meta.get('eligibility',{})
            if name and record.name!=name:continue
            if meta.get('engine')=='spark':
                from quantlens.spark_weight_fit import exposures
                population=exposures(spark,meta['dependencies'],versions=meta.get('feature_versions')).where((F.col('date')>=F.lit(evaluation_start))&(F.col('date')<=F.lit(evaluation_end))).join(universe,['code','date'])
            elif filters:
                population=fields(spark,list(filters),evaluation_start,evaluation_end).join(universe,['code','date'])
                for field,minimum in filters.items():population=population.where(F.col(field)>=minimum)
            else:continue
            counts=population.groupBy('date').agg(F.count('*').alias('_eligible_n'))
            daily=(daily.join(counts,'date','left').withColumn('coverage',F.when((F.col('factor')==record.name)&(F.col('version')==record.version),F.try_divide(F.col('n'),F.col('_eligible_n'))).otherwise(F.col('coverage'))).drop('_eligible_n'))
    window = Window.partitionBy('factor','version','horizon').orderBy('date')
    daily=(daily.withColumn('_previous',F.lag('_members').over(window))
           .withColumn('turnover',F.when(F.col('_previous').isNotNull(),1-F.size(F.array_intersect('_members','_previous'))/F.greatest(F.size('_members'),F.lit(1))))
           .drop('_members','_previous').persist())
    daily_output=lineage(daily,context).withColumn('year',F.year('date'))
    if spark.catalog.tableExists('dws.factor_eval_daily'):
        keys=['factor','version','date','horizon']
        current=spark.table('dws.factor_eval_daily')
        touched=daily_output.select('factor','version','year').distinct()
        retained=current.join(touched,['factor','version','year']).join(daily_output.select(*keys),keys,'left_anti')
        daily_output=retained.unionByName(daily_output)
    publish(spark,daily_output, 'dws.factor_eval_daily',['factor','version','year'],run=context)
    summary = daily.groupBy('factor','version','horizon').agg(F.avg('rank_ic').alias('rank_ic'),F.stddev('rank_ic').alias('ic_std'),F.avg((F.col('rank_ic')>0).cast('double')).alias('positive_ratio'),F.avg('coverage').alias('coverage'),F.avg('turnover').alias('turnover'),F.avg('monotonicity').alias('monotonicity'),*[F.avg(f'q{i}_ret').alias(f'q{i}_ret') for i in range(1,6)])
    quarters = daily.withColumn('_quarter',F.concat_ws('-',F.year('date'),F.quarter('date'))).groupBy('factor','version','horizon','_quarter').agg(F.avg('rank_ic').alias('_ic'))
    stability = quarters.join(summary.select('factor','version','horizon',F.col('rank_ic').alias('_mean')),['factor','version','horizon']).groupBy('factor','version','horizon').agg(F.avg((F.col('_ic')*F.col('_mean')>=0).cast('double')).alias('sign_stability'))
    summary = summary.join(stability,['factor','version','horizon']).withColumn('icir',F.when(F.col('ic_std')>0,F.col('rank_ic')/F.col('ic_std')))
    summary = summary.withColumn('eval_start',F.to_date(F.lit(evaluation_start))).withColumn('eval_end',F.to_date(F.lit(evaluation_end)))
    summary = summary.withColumn('run_id',F.lit(context['run_id'])).withColumn('data_snapshot',F.lit(context['data_snapshot'])).withColumn('as_of',F.current_date())
    if spark.catalog.tableExists('dws.factor_eval'):
        old=spark.table('dws.factor_eval').where(F.date_format('as_of','yyyy-MM-dd')==F.date_format(F.current_date(),'yyyy-MM-dd'))
        keys=['factor','version','horizon','eval_start','eval_end']
        summary=old.join(summary.select(*keys),keys,'left_anti').unionByName(summary)
    publish(spark,summary,'dws.factor_eval',['as_of'],run=context)
    publish(spark,summary,'ads.factor_eval',['as_of'],run=context)
    daily.unpersist()


def correlations(spark,context,name=None,start=None,end=None,*,exclude_model=False):
    from pyspark.sql import functions as F
    from itertools import combinations
    from factors.registry import FactorRegistry
    from quantlens.spark_weight_fit import safe_correlation
    selected=[(meta.name,meta.version) for meta in FactorRegistry().list() if not (exclude_model and meta.name=='model_gbt')]
    if name and not any(item[0]==name for item in selected):return
    # Pivot once and aggregate pairs in place. A self-join multiplies each
    # stock/date observation by the square of the number of factors.
    values=spark.table('dws.factor_values')
    if start:values=values.where(F.col('date')>=start)
    if end:values=values.where(F.col('date')<=end)
    present={(row.factor,row.version) for row in values.select('factor','version').distinct().collect()}
    selected=[item for item in selected if item in present]
    if len(selected)<2:return
    keys=[factor+'@'+version for factor,version in selected]
    filtered=values.withColumn('_identity',F.concat_ws('@','factor','version')).where(F.col('_identity').isin(keys))
    wide=filtered.groupBy('code','date').pivot('_identity',keys).agg(F.first('std_value'))
    pairs=[(left,right) for left,right in combinations(range(len(keys)),2) if name is None or selected[left][0]==name or selected[right][0]==name]
    aggregates=[safe_correlation(F.col('`'+keys[left]+'`'),F.col('`'+keys[right]+'`')).alias('c'+str(index)) for index,(left,right) in enumerate(pairs)]
    row=wide.agg(*aggregates).first()
    rows=[(*selected[left],*selected[right],row[index]) for index,(left,right) in enumerate(pairs)]
    frame=spark.createDataFrame(rows,'factor_a string, version_a string, factor_b string, version_b string, correlation double').withColumn('as_of',F.current_date())
    if name and spark.catalog.tableExists('dws.factor_corr'):
        old=spark.table('dws.factor_corr').where((F.date_format('as_of','yyyy-MM-dd')==F.date_format(F.current_date(),'yyyy-MM-dd'))&(F.col('factor_a')!=name)&(F.col('factor_b')!=name))
        frame=lineage(frame,context).unionByName(old)
    publish(spark,lineage(frame,context),'dws.factor_corr',['as_of'],run=context)


def run(spark,context,name=None,start=None,end=None,*,exclude_model=False):
    from factors.registry import FactorRegistry
    present={(row.factor,row.version) for row in spark.table('dws.factor_values').select('factor','version').distinct().collect()}
    for meta in FactorRegistry().list():
        if exclude_model and meta.name=='model_gbt':continue
        if name and meta.name!=name:continue
        if (meta.name,meta.version) not in present:continue
        print(f'QUANTLENS_FACTOR_EVAL={meta.name}@{meta.version}',flush=True)
        evaluate_one(spark,context,meta.name,meta.version,start,end)
        spark.catalog.clearCache();spark._jvm.System.gc()
    correlations(spark,context,name,start,end,exclude_model=exclude_model)

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--factor');parser.add_argument('--start');parser.add_argument('--end');parser.add_argument('--batch')
    args=parser.parse_args()
    with job('factor_eval') as (spark,context):
        run(spark,context,args.factor,args.start,args.end)
        if args.batch:
            from quantlens.spark_ads import export
            export(spark,context,args.batch)
