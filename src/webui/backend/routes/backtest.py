from __future__ import annotations

import json
from datetime import date
from pydantic import BaseModel, Field
from exceptions import ContractViolation
from benchmarks.models import ReferenceComparison
from ..tasks import manager
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from presentation import ARCHIVED_STRATEGIES, factor_display_name, strategy_display_name
from quantlens import ads
from ..serialize import df_records, downsample_indices, json_safe

router = APIRouter(tags=['backtest'])
NAV_MAX_POINTS = 1500


@router.get('/benchmarks/catalogue')
def get_benchmark_catalogue():
    from benchmarks.catalogue import catalogue, coverage
    from benchmarks.models import BENCHMARK_NAMES
    quotes = coverage()
    return {'source': 'csmar', 'methods': BENCHMARK_NAMES,
            'items': [{**row, 'coverage': quotes.get(row['index_code'])} for row in catalogue()]}


class BacktestRequest(BaseModel):
    strategy_id:str
    start:date
    end:date
    capital:float=Field(default=1000000,gt=0)
    account_id:str=''


@router.post('/backtest/runs')
def start_run(payload:BacktestRequest):
    from backtest.config import BacktestConfig
    from backtest.engine import run_backtest
    if payload.start>=payload.end: raise ContractViolation(code='BACKTEST_BAD_RANGE',message='回测日期顺序错误',suggested_action='结束日期必须晚于开始日期')
    from portfolio.accounts import AccountRegistry
    from portfolio.capital import account_capital
    account = AccountRegistry().resolve(payload.account_id)
    capital = account_capital(account)
    # Freeze account settings before the asynchronous job starts.
    config=BacktestConfig(profile_name=payload.strategy_id,start=str(payload.start),end=str(payload.end),initial_capital=capital,
                          account_id=account.account_id,account_name=account.alias,
                          star_market_enabled=account.star_market_enabled)
    def run(report):
        report('从 DWS 读取因子与权重，执行逐日撮合')
        from backtest.protocol import verify_protocol_or_raise
        from backtest.config import resolve_and_check
        protocol=verify_protocol_or_raise()
        outcome=run_backtest(config,protocol=protocol,profile=resolve_and_check(config,protocol))
        return {'run_id':outcome.run_id,'metrics':outcome.metrics}
    return {'task_id':manager.start('策略回测',run,resource='data'),**ads.provenance()}



def _record(row):
    config=json.loads(row['config'])
    profile=row['strategy_id']
    factors=((config.get('profile') or {}).get('factors') or {}).get('items') or []
    status=json.loads(row['status'])
    if (config.get('protocol') or {}).get('schema_version')!=3:
        raise HTTPException(409,'已发布回测不符合当前协议，必须清理并重新计算。')
    return {'run_id':row['backtest_run'],'profile':profile,'profile_display_name':strategy_display_name(profile),
            'archived':profile in ARCHIVED_STRATEGIES,'factor_display_names':{item['name']:factor_display_name(item['name']) for item in factors},
            'status':status,'config':config,'metrics':json.loads(row['metrics'])}


def _require_valid_run(run_id):
    rows=ads.query('backtest_summary','backtest_run=?',[run_id],limit=1)
    if not rows:
        raise HTTPException(404,'回测结果不存在')
    return _record(rows[0])


@router.get('/backtest/runs')
def list_runs(profile: str = '') -> dict:
    rows=ads.query('backtest_summary','strategy_id=?' if profile else '',[profile] if profile else [],limit=10000,order="TRY_CAST(json_extract_string(status, '$.updated_at') AS TIMESTAMPTZ) DESC NULLS LAST,backtest_run DESC")
    items=[]
    for row in rows:
        record=_record(row);config=record['config'];settings=config.get('config') or {}
        items.append({key:record[key] for key in ['run_id','profile','profile_display_name','archived']} |
                     {'state':record['status'].get('state'),'start':settings.get('start'),'end':settings.get('end'),
                      'account_id':settings.get('account_id'),'account_name':settings.get('account_name'),
                      'star_market_enabled':settings.get('star_market_enabled',True),
                      'protocol_locked':config.get('protocol_locked'),'factor_display_names':list(record['factor_display_names'].values()),
                      'updated_at':record['status'].get('updated_at')})
    def updated_time(item):
        value = pd.to_datetime(item.get('updated_at'), utc=True, errors='coerce')
        return value.value if not pd.isna(value) else pd.NaT.value
    items.sort(key=updated_time, reverse=True)
    return json_safe({'items':items,**ads.provenance('backtest_summary')})


@router.get('/backtest/runs/{run_id}')
def get_run(run_id: str) -> dict:
    rows=ads.query('backtest_summary','backtest_run=?',[run_id],limit=1)
    if not rows:raise HTTPException(404,'回测结果尚未发布')
    provenance = ads.provenance('backtest_summary')
    return json_safe({**provenance, 'publication_run_id': provenance.get('run_id'), **_record(rows[0])})


@router.get('/backtest/runs/{run_id}/nav')
def get_nav(run_id: str) -> dict:
    _require_valid_run(run_id)
    nav=pd.DataFrame(ads.query('backtest_nav','backtest_run=?',[run_id],limit=10000,order='date'))
    if nav.empty:return {'total_rows':0,'items':[],'monthly_returns':[],**ads.provenance('backtest_nav')}
    nav['date']=nav['date'].astype(str)
    nav_values=nav['nav'].astype(float)
    nav['drawdown']=nav_values/nav_values.cummax()-1
    if 'benchmark_return' in nav:
        nav['benchmark_nav']=(1+nav['benchmark_return'].astype(float).fillna(0)).cumprod()
    monthly=[]
    if 'daily_return' in nav:
        grouped=(1+nav['daily_return'].astype(float).fillna(0)).groupby(nav['date'].str[:7]).prod()
        monthly=[{'month':month,'value':float(value)-1} for month,value in grouped.items()]
    picked=downsample_indices(len(nav),NAV_MAX_POINTS,keep=[int(nav.drawdown.idxmin())])
    return {'total_rows':len(nav),'items':df_records(nav.iloc[picked]),'monthly_returns':monthly,**ads.provenance('backtest_nav')}


@router.get('/backtest/runs/{run_id}/references', response_model=ReferenceComparison)
def get_references(run_id: str) -> dict:
    record = _require_valid_run(run_id)
    from benchmarks.comparison import comparison
    from benchmarks.references import reference_for_run
    config = record['config']
    reference = reference_for_run(run_id, config)
    nav = pd.DataFrame(ads.query('backtest_nav', 'backtest_run=?', [run_id], limit=10000, order='date'))
    result = comparison(reference, nav, (config.get('config') or {}).get('initial_capital'))
    if result['items']:
        values = nav['nav'].astype(float)
        picked = downsample_indices(len(nav), NAV_MAX_POINTS, keep=[int((values / values.cummax() - 1).idxmin())])
        result['items'] = [result['items'][i] for i in picked]
    return json_safe(result)


@router.get('/backtest/runs/{run_id}/trades')
def get_trades(run_id: str, limit: int = Query(200,ge=1,le=10000), offset: int = Query(0,ge=0)) -> dict:
    _require_valid_run(run_id)
    return {'total_rows':ads.count('backtest_trades','backtest_run=?',[run_id]),
            'items':ads.query('backtest_trades','backtest_run=?',[run_id],limit=limit,offset=offset,order='date DESC,instrument,side'),**ads.provenance('backtest_trades')}


@router.get('/backtest/runs/{run_id}/attribution')
def get_attribution(run_id: str, limit: int = Query(1000, ge=1, le=10000), offset: int = Query(0, ge=0)) -> dict:
    record = _require_valid_run(run_id)
    where = 'backtest_run=? AND strategy_id=?'
    params = [run_id, record['profile']]
    return {**ads.provenance('attribution'), 'backtest_run': run_id,
            'total_rows': ads.count('attribution', where, params),
            'items': ads.query('attribution', where, params, limit=limit, offset=offset, order='date,factor')}


@router.get('/backtest/runs/{run_id}/positions')
def get_positions(run_id: str, date: str = '', limit: int = Query(500,ge=1,le=10000), offset: int = Query(0,ge=0)) -> dict:
    from bisect import bisect_right
    from quantlens.data import stock_names

    record=_require_valid_run(run_id)
    # Trading dates come from the small NAV extraction; positions remain paged.
    dates=[str(row['date'])[:10] for row in ads.query('backtest_nav','backtest_run=?',[run_id],limit=10000,order='date')]
    settings=(record.get('config') or {}).get('config') or {}
    start=settings.get('start') or (dates[0] if dates else '')
    end=settings.get('end') or (dates[-1] if dates else '')
    target=date or (dates[-1] if dates else '')
    if target and ((start and target<start) or (end and target>end)):
        raise HTTPException(400,'所选日期不在回测区间内')
    # Non-trading days retain the last close's holdings, including an empty portfolio.
    index=bisect_right(dates,target)-1
    snapshot_date=dates[index] if index>=0 else ''
    where='backtest_run=? AND date=?';params=[run_id,snapshot_date or target]
    items=ads.query('backtest_positions',where,params,limit=limit,offset=offset,order='weight DESC,instrument')
    names=stock_names({row['instrument'] for row in items}) if items else {}
    items=[{**row,'stock_name':names.get(row['instrument'], '')} for row in items]
    return {'dates':dates,'date':target,'snapshot_date':snapshot_date,'date_range':{'start':start,'end':end},
            'adjustment_dates':_adjustment_dates(run_id),
            'total_rows':ads.count('backtest_positions',where,params),'items':items,**ads.provenance('backtest_positions')}


def _adjustment_dates(run_id: str) -> list[str]:
    """Read all executed adjustment dates without the trade table's page limit."""
    import duckdb

    files=ads.paths('backtest_trades')
    if not files:
        return []
    with duckdb.connect() as db:
        db.execute('SET threads=2')
        db.execute("SET memory_limit='256MB'")
        rows=db.execute("""SELECT DISTINCT date FROM read_parquet(?, hive_partitioning=false)
            WHERE backtest_run=? AND status IN ('filled','forced_delist_close') AND shares>0
            ORDER BY date""", [files,run_id]).fetchall()
    return [str(row[0])[:10] for row in rows]
