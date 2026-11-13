from __future__ import annotations

import json
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from presentation import ARCHIVED_STRATEGIES, factor_display_name, strategy_display_name
from quantlens import ads
from ..serialize import df_records, downsample_indices, json_safe

router = APIRouter(tags=['backtest'])
NAV_MAX_POINTS = 1500


def _record(row):
    config=json.loads(row['config'])
    profile=row['strategy_id']
    factors=((config.get('profile') or {}).get('factors') or {}).get('items') or []
    return {'run_id':row['backtest_run'],'profile':profile,'profile_display_name':strategy_display_name(profile),
            'archived':profile in ARCHIVED_STRATEGIES,'factor_display_names':{item['name']:factor_display_name(item['name']) for item in factors},
            'status':json.loads(row['status']),'config':config,'metrics':json.loads(row['metrics'])}


@router.get('/backtest/runs')
def list_runs(profile: str = '') -> dict:
    rows=ads.query('backtest_summary','strategy_id=?' if profile else '',[profile] if profile else [],limit=10000,order='backtest_run DESC')
    items=[]
    for row in rows:
        record=_record(row);config=record['config'];settings=config.get('config') or {}
        items.append({key:record[key] for key in ['run_id','profile','profile_display_name','archived']} |
                     {'state':record['status'].get('state'),'start':settings.get('start'),'end':settings.get('end'),
                      'account_id':settings.get('account_id'),'account_name':settings.get('account_name'),
                      'star_market_enabled':settings.get('star_market_enabled',True),
                      'protocol_locked':config.get('protocol_locked'),'factor_display_names':list(record['factor_display_names'].values())})
    return json_safe({'items':items,**ads.provenance('backtest_summary')})


@router.get('/backtest/runs/{run_id}')
def get_run(run_id: str) -> dict:
    rows=ads.query('backtest_summary','backtest_run=?',[run_id],limit=1)
    if not rows:raise HTTPException(404,'回测结果尚未发布')
    return json_safe({**_record(rows[0]),**ads.provenance('backtest_summary')})


@router.get('/backtest/runs/{run_id}/nav')
def get_nav(run_id: str) -> dict:
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


@router.get('/backtest/runs/{run_id}/trades')
def get_trades(run_id: str, limit: int = Query(200,ge=1,le=10000), offset: int = Query(0,ge=0)) -> dict:
    return {'total_rows':ads.count('backtest_trades','backtest_run=?',[run_id]),
            'items':ads.query('backtest_trades','backtest_run=?',[run_id],limit=limit,offset=offset,order='date,instrument,side'),**ads.provenance('backtest_trades')}


@router.get('/backtest/runs/{run_id}/positions')
def get_positions(run_id: str, date: str = '', limit: int = Query(500,ge=1,le=10000)) -> dict:
    # Trading dates come from the small NAV extraction; positions remain paged.
    dates=[row['date'] for row in ads.query('backtest_nav','backtest_run=?',[run_id],limit=10000,order='date')]
    target=date or (dates[-1] if dates else '')
    where='backtest_run=? AND date=?';params=[run_id,target]
    return {'dates':dates,'date':target,'total_rows':ads.count('backtest_positions',where,params),
            'items':ads.query('backtest_positions',where,params,limit=limit,order='weight DESC,instrument'),**ads.provenance('backtest_positions')}
