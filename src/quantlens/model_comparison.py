"""Common held-out diagnostics for weighting and dimensionality research."""
import numpy as np
import pandas as pd


def summarize(scored):
    def daily(pdf):
        valid=pdf[['code','score','ret']].replace([np.inf,-np.inf],np.nan).dropna()
        chosen=valid.sort_values(['score','code'],ascending=[False,True]).head(max(1,len(valid)//5))
        return pd.DataFrame([{'date':pdf.date.iloc[0],'rank_ic':valid.score.rank().corr(valid.ret.rank()) if len(valid)>2 else np.nan,
                              'top_return':chosen.ret.mean(),'members':','.join(chosen.code)}])
    rows=scored.select('code','date','score','ret').groupBy('date').applyInPandas(daily,'date date, rank_ic double, top_return double, members string').orderBy('date').toPandas()
    cohort=rows.iloc[::5].copy()
    if cohort.empty:raise ValueError('No mature held-out observations')
    nav=(1+cohort.top_return.fillna(0)).cumprod();drawdown=abs(float((nav/nav.cummax()-1).min()))
    previous=set();turnover=[]
    for members in cohort.members:
        current=set(members.split(',')) if members else set()
        turnover.append(1-len(previous&current)/max(1,len(current)));previous=current
    annual=float(nav.iloc[-1]**(244/(len(cohort)*5))-1)
    return {'rank_ic':float(rows.rank_ic.mean()),'top_quintile_return':float(nav.iloc[-1]-1),
            'turnover':float(np.mean(turnover))*244/5,'max_drawdown':drawdown,
            'objective_value':annual/drawdown if drawdown else 0.,'evaluation':'nonoverlapping_5d_gross_cohorts'}


def apply_conclusions():
    from quantlens.ads import query
    from factors.registry import FactorRegistry,FactorStatus
    version=FactorRegistry().get('model_gbt').version
    rows=query('model_comparison',"experiment='gbt' AND factor_version=?",[version],limit=10000,order='as_of DESC,test_start')
    if not rows:return
    scores={}
    seen=set()
    for row in rows:
        key=(row['test_start'],row['method'])
        if key in seen:continue
        seen.add(key);scores.setdefault(row['method'],[]).append(row['objective_value'])
    if not all(method in scores for method in ['model_gbt','icir','model_linear']):return
    means={key:float(np.mean(value)) for key,value in scores.items()}
    if means['model_gbt']<max(means['icir'],means['model_linear']):
        registry=FactorRegistry();meta=registry.get('model_gbt')
        if meta.status==FactorStatus.DISABLED:return False
        meta.status=FactorStatus.DISABLED
        meta.risk_notes='样本外同窗口评估未胜过 ICIR/线性加权；保留研究结论。'+str(means)
        registry.upsert(meta,override=True)
        return True
    return False


def publish_comparison(spark,frame,context,experiment):
    from pyspark.sql import functions as F,Window
    from quantlens.warehouse import lineage,publish
    family=F.regexp_replace('method','^(raw|pca)_','') if experiment=='pca' else F.lit('composite')
    ranked=frame.withColumn('_family',family)
    order=Window.partitionBy('experiment','test_start','_family').orderBy(F.col('objective_value').desc_nulls_last(),'method')
    ranked=ranked.withColumn('selected_in_comparison',F.row_number().over(order)==1).drop('_family')
    output=lineage(ranked.withColumn('comparison_metric',F.lit('calmar')),context)
    if spark.catalog.tableExists('ads.model_comparison'):
        old=spark.table('ads.model_comparison').where((F.date_format('as_of','yyyy-MM-dd')==F.date_format(F.current_date(),'yyyy-MM-dd'))&(F.col('experiment')!=experiment))
        output=old.unionByName(output)
    publish(spark,output,'ads.model_comparison',['as_of'],run=context)
