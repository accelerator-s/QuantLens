from __future__ import annotations
import warnings
import numpy as np
import pandas as pd
from exceptions import ContractViolation
from factors.registry import FactorRegistry
from factors.sandbox import execute_factor
from quantlens.warehouse import job, lineage, publish


def windows(dates, min_history, window_days, instruments, dependencies, memory_mb=1536):
    dates = list(dates)
    while (min_history+window_days)*instruments*dependencies*8*3 > memory_mb*1024**2*.5:
        if window_days == 1:
            raise ContractViolation(code='FACTOR_WINDOW_MEMORY',message='历史窗口超过执行器内存预算',suggested_action='增加执行器内存或减少字段；不得拆分横截面')
        window_days = max(1,window_days//2)
        warnings.warn(f'缩小因子窗口至 {window_days} 天', RuntimeWarning)
    return [(i//window_days, dates[max(0,i-min_history)], dates[i], dates[min(i+window_days,len(dates))-1]) for i in range(0,len(dates),window_days)]


def run_window(data, source, dependencies):
    # Every window contains all stocks. No stock partitioning of cross sections.
    data = data.copy()
    data['date'] = pd.to_datetime(data['date'])
    panel = {dep:data.pivot(index='date',columns='code',values=dep).sort_index() for dep in dependencies}
    value = execute_factor(source, panel)
    if not isinstance(value,pd.DataFrame): raise TypeError('compute(panel) must return a DataFrame')
    start,end = pd.to_datetime(data['_output_start'].iloc[0]),pd.to_datetime(data['_output_end'].iloc[0])
    value = value.loc[(value.index>=start)&(value.index<=end)]
    value = value.rename_axis(index='date',columns='code').stack().rename('value').reset_index()
    value['date'] = value['date'].dt.date
    value['value'] = value['value'].replace([np.inf,-np.inf],np.nan).round(6)
    return value[['code','date','value']]


def deps_for_gate(meta):
    required=set(meta.dependencies)
    from quantlens.standardize import settings
    steps,missing=settings(meta.standardization,meta.missing_handling)
    if 'industry_neutral' in steps or missing=='fill':required.add('industry_code')
    if 'size_neutral' in steps:required.add('total_mv')
    return sorted(required)

def run(spark, context, names=None, start=None, end=None):
    from pyspark.sql import functions as F
    from quantlens.panel_source import fields
    from quantlens.standardize import standardize
    from quantlens.sync_factor_meta import metadata
    from paths import workspace
    versions = {m['name']:m for m in metadata(spark)}
    dates = [r.date for r in spark.table('dwd.prices_clean').select('date').distinct().orderBy('date').collect()]
    instruments = spark.table('dwd.prices_clean').select('code').distinct().count()
    results,failures = [],[]
    for meta in FactorRegistry().list(include_research=bool(names)):
        if meta.name == 'model_gbt' or (names and meta.name not in names): continue
        print('QUANTLENS_FACTOR='+meta.name,flush=True)
        inputs=None
        try:
            required = sorted(set(meta.dependencies)|{'industry_code','mktcap_total'})
            bounds = windows(dates,meta.min_history,meta.window_days,instruments,len(required))
            if start or end:
                from datetime import date
                lower=date.fromisoformat(start) if start else dates[0]
                upper=date.fromisoformat(end) if end else dates[-1]
                bounds=[(key,history,max(first,lower),min(last,upper)) for key,history,first,last in bounds if last>=lower and first<=upper]
            if not bounds:raise ValueError('No trading dates in factor window')
            inputs = fields(spark, required,bounds[0][1],bounds[-1][3]).withColumn('total_mv',F.col('mktcap_total')).persist()
            # Gate dependency coverage only on dates after the declared warmup.
            maturity = dates[min(meta.min_history,len(dates)-1)]
            eligible = spark.table('dwd.universe').where('in_universe').select('code','date')
            check = inputs.join(eligible,['code','date']).where(F.col('date')>=F.lit(maturity))
            if start:check=check.where(F.col('date')>=start)
            if end:check=check.where(F.col('date')<=end)
            for field,minimum in meta.eligibility.items():check=check.where(F.col(field)>=minimum)
            coverage = check.agg(*[F.avg(F.col(dep).isNotNull().cast('double')).alias(dep) for dep in deps_for_gate(meta)]).first().asDict()
            gaps = {name:ratio for name,ratio in coverage.items() if ratio is None or ratio <= .90}
            if gaps:
                from quantlens.warehouse import write_json,root_uri
                write_json(spark,f'{root_uri()}/dws/factor_values/_blocked/{context["run_id"]}/{meta.name}',{'factor':meta.name,'dependencies':gaps,'threshold':.90,**context})
                raise ContractViolation(code='FACTOR_INPUT_COVERAGE',message=f'{meta.name} 输入覆盖不足: {gaps}',suggested_action='补齐源数据后重跑该因子')
            window_frame = spark.createDataFrame(bounds,'window_id int, _history_start date, _output_start date, _output_end date')
            expanded = inputs.join(F.broadcast(window_frame), (inputs.date>=window_frame._history_start)&(inputs.date<=window_frame._output_end))
            source = (workspace().root/meta.plugin_path).read_text()
            deps = meta.dependencies
            raw = expanded.groupBy('window_id').applyInPandas(lambda pdf:run_window(pdf,source,deps),'code string, date date, value double')
            if meta.eligibility:
                population=inputs
                for field,minimum in meta.eligibility.items():population=population.where(F.col(field)>=minimum)
                raw=raw.join(population.select('code','date'),['code','date'])
            aux = inputs.select('code','date','industry_code','total_mv')
            def transform(pdf):
                return standardize(pdf, meta.standardization, meta.missing_handling)[['code','date','value','std_value','coverage']]
            result = raw.join(aux,['code','date'],'left').groupBy('date').applyInPandas(transform,'code string, date date, value double, std_value double, coverage double')
            result = (lineage(result,context,meta.version).withColumn('factor',F.lit(meta.name))
                      .withColumn('code_hash',F.lit(versions[meta.name]['code_hash'])).withColumn('year',F.year('date'))
                      .orderBy('factor','date','code'))
            if (start or end) and spark.catalog.tableExists('dws.factor_values'):
                keys=['factor','version','date','code']
                touched=result.select('factor','version','year').distinct()
                retained=spark.table('dws.factor_values').join(touched,['factor','version','year']).join(result.select(*keys),keys,'left_anti')
                result=retained.unionByName(result)
            results.append(publish(spark,result,'dws.factor_values',['factor','version','year'],run=context))
            print('QUANTLENS_FACTOR_PUBLISHED='+meta.name,flush=True)
            inputs.unpersist()
        except Exception as exc:
            if inputs is not None: inputs.unpersist()
            failures.append({'factor':meta.name,'message':str(exc)})
            print('QUANTLENS_FACTOR_BLOCKED='+meta.name+': '+str(exc),flush=True)
        finally:
            inputs=None;result=None;raw=None;expanded=None;aux=None;check=None
            spark.catalog.clearCache()
            spark._jvm.System.gc()
    if failures:
        raise ContractViolation(code='FACTOR_BATCH_FAILED',message=str(failures),suggested_action='失败因子未发布；查看窗口错误和字段覆盖率')
    return results

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--factor',action='append');parser.add_argument('--start');parser.add_argument('--end');args=parser.parse_args()
    with job('factor_values') as (spark, context):
        from quantlens.sync_factor_meta import run as sync_meta
        sync_meta(spark,context)
        run(spark,context,args.factor,args.start,args.end)
