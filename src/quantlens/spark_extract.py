"""Bounded research extracts from published warehouse tables, never ODS."""
import json
from quantlens.warehouse import job,root_uri,write_json


def run(spark,context,request_id,query):
    from pyspark.sql import functions as F
    kind=query['kind'];upstream={}
    if kind=='panel':
        from quantlens.panel_source import fields
        frame=fields(spark,query['fields'],query.get('start'),query.get('end'))
    elif kind=='signal':
        from strategy import ResolvedStrategyProfile
        from quantlens.signals import composite
        profile=ResolvedStrategyProfile.model_validate(query['profile'])
        frame,upstream=composite(spark,profile,query['start'],query['end'])
    elif kind=='signal_diagnostics':
        from strategy import ResolvedStrategyProfile
        from factor_evolution.signal_diagnostics import extract_daily
        profile=ResolvedStrategyProfile.model_validate(query['profile'])
        frame,upstream=extract_daily(spark,profile,query['start'],query['end'],query['horizons'])
    elif kind=='factor':
        frame=spark.table('dws.factor_values').where((F.col('factor')==query['factor'])&(F.col('version')==query['version'])).select('code','date','value','std_value','run_id','data_snapshot')
    elif kind=='labels':
        frame=spark.table('dwd.label_forward').where((F.col('horizon')==query['horizon'])&(F.col('label_version')==query.get('label_version','v1')))
    else:raise ValueError('Unknown research extract kind')
    if query.get('start'):frame=frame.where(F.col('date')>=query['start'])
    if query.get('end'):frame=frame.where(F.col('date')<=query['end'])
    uri=f'{root_uri()}/meta/extracts/{request_id}/data'
    frame.coalesce(4).write.mode('errorifexists').option('compression','snappy').parquet(uri)
    write_json(spark,f'{root_uri()}/meta/extracts/{request_id}/manifest',{'source_uri':uri,'rows':frame.count(),'query':query,'upstream':upstream,**context})

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--query',required=True);args=parser.parse_args()
    with job('research_extract') as (spark,context):run(spark,context,args.request,json.loads(args.query))
