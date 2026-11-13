from __future__ import annotations
import json
from pathlib import Path
import duckdb
from exceptions import ContractViolation
from paths import workspace
from webui.backend.serialize import json_safe

TABLES={'factor_eval_daily','model_comparison','factor_meta','factor_eval','factor_values','factor_weights','strategy_signal','backtest_summary','backtest_nav','backtest_trades','backtest_positions','attribution','portfolio_target','weighting_compare','audit_log','overview','market','stock_prices','stock_fundamentals','stock_analyst','stock_industry','screen','quality_report'}


def manifest():
    path=workspace().data/'ads_manifest.json'
    return json.loads(path.read_text()) if path.exists() else {'tables':{},'data_as_of':None,'run_id':None}


def provenance(table=None):
    doc=manifest();entry=doc.get('tables',{}).get(table,{}) if table else doc
    return {'data_as_of':entry.get('data_as_of',doc.get('data_as_of')),'layer':'ads','run_id':entry.get('run_id',doc.get('run_id')),
            'source_uri':entry.get('source_uri'),'data_snapshot':entry.get('data_snapshot',doc.get('data_snapshot'))}


def paths(table):
    if table not in TABLES: raise ValueError('Unknown ADS table')
    entry=manifest().get('tables',{}).get(table)
    if not entry: return []
    root=(workspace().data/'ads').resolve()
    files=[]
    for file in entry['files']:
        path=(root/file['path']).resolve()
        if root not in path.parents: raise ValueError('ADS path escapes root')
        if not path.is_file():
            raise ContractViolation(code='ADS_FILE_MISSING',message=f'{table} 已发布文件缺失',suggested_action='重新同步 ADS 批次')
        files.append(str(path))
    return files


def query(table,where='',params=(),*,limit=100,offset=0,order=''):
    files=paths(table)
    if not files: return []
    if not 1<=limit<=10000 or offset<0: raise ValueError('Invalid pagination')
    with duckdb.connect() as db:
        db.execute('SET threads=2')
        db.execute("SET memory_limit='1GB'")
        sql='SELECT * FROM read_parquet(?, hive_partitioning=false)'
        if where: sql+=' WHERE '+where
        if order: sql+=' ORDER BY '+order
        sql+=' LIMIT ? OFFSET ?'
        result=db.execute(sql,[files,*params,limit,offset])
        columns=[item[0] for item in result.description]
        return json_safe([dict(zip(columns,row)) for row in result.fetchall()])


def response(table,**kwargs):
    return {'items':query(table,**kwargs),**provenance(table)}


def count(table,where='',params=()):
    files=paths(table)
    if not files:return 0
    with duckdb.connect() as db:
        sql='SELECT count(*) FROM read_parquet(?, hive_partitioning=false)'
        if where:sql+=' WHERE '+where
        return db.execute(sql,[files,*params]).fetchone()[0]
