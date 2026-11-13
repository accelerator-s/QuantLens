"""Serving data is extracted ADS; no product query reads staged or ODS."""
from datetime import datetime,date
from zoneinfo import ZoneInfo
from quantlens import ads,store
from exceptions import ContractViolation


def coverage():
    rows=ads.query('overview',limit=1,order='as_of DESC')
    value=rows[0] if rows else {'start':None,'end':None,'rows':0,'instruments':0,'days':0,'factor_evaluations':0}
    value['age_days']=(datetime.now(ZoneInfo('Asia/Shanghai')).date()-date.fromisoformat(str(value['end'])[:10])).days if value['end'] else None
    return {**value,**ads.provenance('overview')}


def inventory():
    return [{'table':name,'rows':entry.get('rows',0),'source_uri':entry['source_uri'],'run_id':entry['run_id']} for name,entry in ads.manifest()['tables'].items()]


def market(as_of=None):
    return ads.query('market',where='date<=CAST(? AS DATE)' if as_of else '',params=[as_of] if as_of else [],limit=120,order='date DESC')


def stock(code,as_of=None):
    end=as_of or coverage()['end']
    prices=ads.query('stock_prices','code=? AND date<=CAST(? AS DATE)',[code,end],limit=500,order='date DESC')
    if not prices: raise ContractViolation(code='STOCK_NOT_PUBLISHED',message='该证券尚无已发布的 ADS 行情',suggested_action='检查证券、日期和数据批次')
    fundamentals=ads.query('stock_fundamentals','code=? AND effective_date<=CAST(? AS DATE)',[code,end],limit=500,order='effective_date DESC,report_period DESC')
    industry=ads.query('stock_industry','code=? AND effective_date<=CAST(? AS DATE)',[code,end],limit=10,order='effective_date DESC')
    values=ads.query('factor_values','code=? AND date=CAST(? AS DATE)',[code,prices[0]['date']],limit=100)
    signals=ads.query('strategy_signal','code=? AND as_of<=CAST(? AS DATE)',[code,end],limit=100,order='as_of DESC')
    analyst=ads.query('stock_analyst','code=? AND effective_date<=CAST(? AS DATE)',[code,end],limit=100,order='effective_date DESC')
    events={name:[] for name in ['fs_income','fs_balance','fs_cashflow','fundamental_ttm','holder_longline','consensus_daily','industry_classification']}
    grouped={}
    for row in fundamentals+analyst:
        name=row.get('statement','consensus_daily')
        key=(name,row.get('report_period'),row['effective_date'])
        item=grouped.setdefault(key,{'report_period':row.get('report_period'),'effective_date':row['effective_date'],'run_id':row.get('run_id')})
        item[row['item']]=row['value']
    for (name,_,_),row in grouped.items():
        if name in events:events[name].append(row)
    events['industry_classification']=industry
    for row in prices:row['instrument_id']=row['code'];row['adj_close_wd']=row['adj_close']
    timeline=[r for r in store.records('watch_event',2000) if r['body']['instrument_id']==code]
    return {'instrument_id':code,'as_of':end,'prices':prices[::-1],'fundamentals':fundamentals,'industry':industry,'factors':values,'signals':signals,'timeline':timeline,'events':events,'pit_note':'DWD 实际可见日对齐；数据来自已发布的 ADS 批次。',**ads.provenance('stock_prices')}
