from __future__ import annotations
import json
import numpy as np
import pandas as pd
from factors.registry import FactorRegistry
from quantlens.warehouse import job, lineage, publish
from exceptions import ContractViolation

METHODS = ('model_linear','model_logistic','equal','ic','icir')


def safe_correlation(left,right):
    from pyspark.sql import functions as F
    left=F.col(left) if isinstance(left,str) else left
    right=F.col(right) if isinstance(right,str) else right
    paired=left.isNotNull()&right.isNotNull()
    x=F.when(paired,left);y=F.when(paired,right)
    return F.try_divide(F.covar_pop(x,y),F.sqrt(F.var_pop(x)*F.var_pop(y)))


def normalize(coefficients):
    coefficients = np.asarray(coefficients,dtype=float)
    total = np.abs(coefficients).sum()
    if not np.isfinite(coefficients).all() or not total>0:
        raise ContractViolation(code='WEIGHTS_DEGENERATE',message='权重不可归一化',suggested_action='检查训练样本、方向和共线性')
    return coefficients/total


def training_bounds(test_year, train_years):
    return f'{test_year-train_years}-01-01', f'{test_year-1}-12-31', f'{test_year}-01-01', f'{test_year}-12-31'


def exposures(spark, names, directions=None,versions=None):
    from pyspark.sql import functions as F
    values = spark.table('dws.factor_values')
    clauses = []
    for name in names:
        meta = FactorRegistry().get(name)
        if meta.direction not in {'positive','negative'}:
            raise ContractViolation(code='FACTOR_DIRECTION_REQUIRED',message=f'{name} 方向未确认',suggested_action='明确因子方向后再参与策略')
        direction=(directions or {}).get(name,-1 if meta.direction=='negative' else 1)
        version=(versions or {}).get(name,meta.version)
        clauses.append(values.where((F.col('factor')==name)&(F.col('version')==version)).select('code','date', F.lit(name).alias('factor'), (F.col('std_value')*direction).alias('std_value')))
    data = clauses[0]
    for frame in clauses[1:]: data = data.unionByName(frame)
    return data.groupBy('code','date').pivot('factor',names).agg(F.first('std_value')).dropna(subset=names)


def fit(spark, train, names, method):
    from pyspark.sql import functions as F, Window
    if method=='equal': return normalize(np.ones(len(names)))
    if method in {'ic','icir'}:
        result=[]
        for name in names:
            # Average ranks preserve the same tie convention as the Pandas evaluator.
            def average_rank(col):
                low = F.rank().over(Window.partitionBy('date').orderBy(col))
                ties = F.count('*').over(Window.partitionBy('date',col))
                return low+(ties-1)/2
            ranked = train.withColumn('_x',average_rank(name)).withColumn('_y',average_rank('ret'))
            daily = ranked.groupBy('date').agg(safe_correlation('_x','_y').alias('ic'))
            row = daily.agg(F.avg('ic').alias('mean'),F.stddev('ic').alias('std')).first()
            result.append((row.mean or 0)/(row.std if method=='icir' and row.std else 1))
        return normalize(result)
    if method in {'model_linear', 'model_ridge'}:
        window = Window.partitionBy('date')
        train = train.withColumn('_label',F.col('ret')-F.avg('ret').over(window))
        columns=[]
        for i,name in enumerate(names):
            column=f'_demeaned_{i}'
            train=train.withColumn(column,F.col(name)-F.avg(name).over(window));columns.append(column)
        from quantlens.gpu import request
        return normalize(request('ridge' if method == 'model_ridge' else 'linear',
                                 train=train,features=columns,label='_label')['coefficients'])
    elif method=='model_logistic':
        medians=train.groupBy('date').agg(F.percentile_approx('ret',.5,10000).alias('_median'))
        train=train.join(medians,'date').withColumn('_label',(F.col('ret')>F.col('_median')).cast('double'))
        from quantlens.gpu import request
        return normalize(request('logistic',train=train,features=names,label='_label')['coefficients'])
    else: raise ValueError(method)


def run(spark, context, strategy_id, train_years=3,profile_payload=None,test_year=None):
    from pyspark.sql import functions as F
    from strategy import StrategyProfileStore,ResolvedStrategyProfile
    profile=ResolvedStrategyProfile.model_validate(profile_payload) if profile_payload else StrategyProfileStore().resolve(strategy_id)
    names=[ref.name for ref in profile.factors.items]
    from backtest.signal import _resolve_direction
    directions={ref.name:_resolve_direction(ref.name,ref.direction_override,FactorRegistry().get(ref.name).direction) for ref in profile.factors.items}
    versions={name:FactorRegistry().get(name).version for name in names}
    sources={}
    for name in names:
        source=spark.table('dws.factor_values').where((F.col('factor')==name)&(F.col('version')==versions[name]))
        sources[name]=[row.asDict() for row in source.select('code_hash','data_snapshot','run_id').distinct().collect()]
    frame=exposures(spark,names,directions).join(spark.table('dwd.label_forward').where("horizon=5 AND label_version='v1' AND valid").select('code','date','ret','valid_until'),['code','date'])
    frame=frame.join(spark.table('dwd.universe').where('in_universe').select('code','date'),['code','date'])
    if test_year:frame=frame.where((F.col('date')>=f'{test_year-train_years}-01-01')&(F.col('date')<=f'{test_year}-12-31'))
    years=[r.year for r in frame.select(F.year('date').alias('year')).distinct().orderBy('year').collect()]
    rows=[]; comparisons=[]
    for year in years:
        if test_year and year!=test_year:continue
        if year<min(years)+train_years: continue
        start,end,test_start,test_end=training_bounds(year,train_years)
        train=frame.where((F.col('date')>=start)&(F.col('date')<=end)&(F.col('valid_until')<test_start)).persist()
        test=frame.where((F.col('date')>=test_start)&(F.col('date')<=test_end)&(F.col('valid_until')<=test_end)).persist()
        if not train.take(1) or not test.take(1): train.unpersist();test.unpersist();continue
        for method in METHODS:
            weights=fit(spark,train,names,method)
            score=sum(F.col(n)*float(w) for n,w in zip(names,weights))
            scored=test.withColumn('score',score)
            from quantlens.model_comparison import summarize
            metrics=summarize(scored)
            for name,weight in zip(names,weights): rows.append((strategy_id,test_start,name,float(weight),method,start,end,json.dumps(metrics),versions[name],json.dumps(sources[name])))
            comparisons.append((strategy_id,test_start,method,*[metrics[k] for k in ['rank_ic','top_quintile_return','turnover','max_drawdown','objective_value']],metrics['evaluation']))
        train.unpersist();test.unpersist()
    if not rows: raise ContractViolation(code='TRAINING_WINDOW_EMPTY',message='没有完整滚动训练窗口',suggested_action='补齐至少 train_years+1 年数据')
    weights=spark.createDataFrame(rows,'strategy_id string, as_of string, factor string, weight double, method string, train_start string, train_end string, metrics string, factor_version string, sources string')
    weights=weights.withColumn('as_of',F.to_date('as_of')).withColumn('train_start',F.to_date('train_start')).withColumn('train_end',F.to_date('train_end'))
    publish(spark,lineage(weights,context),'dws.factor_weights',['strategy_id','as_of'],run=context)
    comparison=spark.createDataFrame(comparisons,'strategy_id string, as_of string, method string, rank_ic double, top_quintile_return double, turnover double, max_drawdown double, objective_value double, evaluation string').withColumn('as_of',F.to_date('as_of'))
    output=lineage(comparison,context)
    if spark.catalog.tableExists('ads.weighting_compare'):
        dates=[str(row.as_of) for row in comparison.select('as_of').distinct().collect()]
        old=spark.table('ads.weighting_compare').where(F.date_format('as_of','yyyy-MM-dd').isin(dates))
        keys=['strategy_id','as_of','method']
        output=old.join(output.select(*keys),keys,'left_anti').unionByName(output)
    publish(spark,output,'ads.weighting_compare',['as_of'],run=context)
    return {'comparisons':[row.asDict() for row in comparison.collect()],**context}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--strategy',required=True);parser.add_argument('--train-years',type=int,default=3);parser.add_argument('--profile');parser.add_argument('--test-year',type=int)
    args=parser.parse_args()
    with job('weight_fit') as (spark,context):
        result=run(spark,context,args.strategy,args.train_years,json.loads(args.profile) if args.profile else None,args.test_year)
        print('QUANTLENS_WEIGHT_COMPARE='+json.dumps(result,default=str),flush=True)
