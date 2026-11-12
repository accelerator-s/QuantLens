import json
import math

import duckdb
import numpy as np
import pandas as pd

from quantlens import store
from quantlens.spark_features import FEATURES, LABELS

def evaluate(root, config, radius):
    with duckdb.connect() as db:
        db.execute("SET threads=4")
        db.execute("SET memory_limit='3GB'")
        db.read_parquet(str(root/'predictions'/'*.parquet')).create_view('predictions')
        db.execute('CREATE TEMP TABLE test AS SELECT * FROM predictions WHERE date>? AND label IS NOT NULL', [config.valid_end])
        headline = db.execute('''SELECT count(*) samples, avg(abs(label-prediction)) mae,
            sqrt(avg(pow(label-prediction,2))) rmse, sqrt(avg(pow(label,2))) zero_rmse,
            avg(CAST((prediction>=0)=(label>=0) AS DOUBLE)) direction_accuracy,
            avg(CAST(abs(label-prediction)<=? AS DOUBLE)) interval_coverage
            FROM test''',[radius]).df().to_dict('records')[0]
        daily = db.execute('''WITH ranks AS (SELECT *,
            rank() OVER(PARTITION BY date ORDER BY prediction)+
                (count(*) OVER(PARTITION BY date,prediction)-1)/2.0 AS rp,
            rank() OVER(PARTITION BY date ORDER BY label)+
                (count(*) OVER(PARTITION BY date,label)-1)/2.0 AS ry,
            rank() OVER(PARTITION BY date ORDER BY momentum_20)+
                (count(*) OVER(PARTITION BY date,momentum_20)-1)/2.0 AS rb
            FROM test)
            SELECT date, count(*) samples, corr(rp,ry) rank_ic, corr(rb,ry) baseline_ic,
            avg(abs(label-prediction)) mae FROM ranks GROUP BY date ORDER BY date''').df()
        groups = db.execute('''WITH ranked AS (SELECT *, ntile(5) OVER(PARTITION BY date ORDER BY prediction,instrument_id) AS bucket FROM test),
            per_day AS (SELECT date,bucket,avg(label) AS forward_return,count(*) AS samples FROM ranked GROUP BY date,bucket)
            SELECT bucket,avg(forward_return) mean_return,sum(samples) samples FROM per_day GROUP BY bucket ORDER BY bucket''').df()
        factor_rows = []
        for feature,label in zip(FEATURES,LABELS):
            value = db.execute(f'''WITH ranks AS (SELECT date,
                rank() OVER(PARTITION BY date ORDER BY {feature})+(count(*) OVER(PARTITION BY date,{feature})-1)/2.0 AS x,
                rank() OVER(PARTITION BY date ORDER BY label)+(count(*) OVER(PARTITION BY date,label)-1)/2.0 AS y
                FROM predictions WHERE date<=? AND label_end<=? AND label IS NOT NULL AND {feature} IS NOT NULL),
                daily AS (SELECT date,corr(x,y) AS ic FROM ranks GROUP BY date)
                SELECT avg(ic) AS rank_ic,stddev_samp(ic) AS ic_std,count(ic) AS dates FROM daily''',
                [config.train_end,config.train_end]).df().to_dict('records')[0]
            factor_rows.append({'feature':feature,'label':label,**value,'scope':'train'})
        prices = db.execute('SELECT * EXCLUDE('+','.join(FEATURES)+') FROM predictions WHERE date>? ORDER BY date,instrument_id',[config.valid_end]).df()
        # 补齐未入选行情
        cfg=json.loads((root/'config.json').read_text())
        from paths import workspace
        source=workspace().data/'snapshots'/cfg['dataset_id']
        market=db.execute('''SELECT p.instrument_id,p.date,p.close,p.high,p.low,p.volume,r.ret_wd,r.trading_status
            FROM read_parquet(?) p JOIN read_parquet(?) r USING(instrument_id,date)
            WHERE CAST(p.date AS DATE)>? AND CAST(p.date AS DATE)<=? ORDER BY p.date''',
            [str(source/'prices_daily.parquet'),str(source/'returns_daily.parquet'),config.valid_end,config.end]).df()
        market['date']=pd.to_datetime(market['date'])
        benchmark = market.groupby('date')['ret_wd'].mean()
        simulation = simulate(prices,market,config.top_n,config.cost_bps)
        simulation['benchmark'] = [{'date':str(d.date()),'nav':float(v)} for d,v in (1+benchmark.fillna(0)).cumprod().items()]
    daily.to_parquet(root/'daily_metrics.parquet',index=False)
    store.write_json(root/'simulation.json',simulation)
    return {'metrics':{**headline,'rank_ic':float(daily.rank_ic.mean()),'baseline_rank_ic':float(daily.baseline_ic.mean()),
                       'positive_ic_days':float((daily.rank_ic>0).mean())},
            'groups':groups.to_dict('records'),'factors':factor_rows,
            'daily':daily.to_dict('records'), 'simulation':{k:v for k,v in simulation.items() if k not in ['nav','trades','benchmark']},
            'notes':['特征筛选与填补只使用训练期；误差区间来自验证期。',
                     '五日标签有重叠；每日IC均值不代表独立样本显著性。',
                     '历史时间外评估；旧项目可能已研究同一时期。',
                     '等权市场收益为统计参照，未扣交易费用。']}

def simulate(predictions, market, top_n, cost_bps, initial=1000000.0):
    predictions=predictions.copy()
    predictions['date']=pd.to_datetime(predictions.date)
    market=market.copy()
    market['date']=pd.to_datetime(market.date)
    signals={d:g.sort_values(['prediction','instrument_id'],ascending=[False,True]).head(top_n)
             for d,g in predictions.groupby('date')}
    market_days={d:g.set_index('instrument_id') for d,g in market.groupby('date')}
    dates=sorted(market_days)
    holdings={}
    cash=initial
    nav=[]
    trades=[]
    pending=None
    failed=0
    fees=0.0
    turnover=0.0
    for i,d in enumerate(dates):
        rows=market_days[d]
        for code,pos in holdings.items():
            if code in rows.index:
                row=rows.loc[code]
                if pd.notna(row.ret_wd):
                    pos['value']*=1+float(row.ret_wd)
                pos['price']=float(row.close)
        equity=cash+sum(p['value'] for p in holdings.values())
        if pending is not None:
            selected=list(pending.instrument_id)
            target=equity/max(top_n,1)
            # 先卖后买
            for code in list(holdings):
                pos=holdings[code]
                desired=target if code in selected else 0
                sell=max(0,pos['value']-desired)
                if sell<1: continue
                if code not in rows.index or rows.loc[code].volume<=0 or rows.loc[code].high==rows.loc[code].low:
                    failed+=1
                    continue
                charge=sell*cost_bps/10000
                cash+=sell-charge
                pos['value']-=sell
                fees+=charge;turnover+=sell
                trades.append({'date':str(d.date()),'instrument_id':code,'side':'sell','amount':sell,'fees':charge})
                if pos['value']<1:del holdings[code]
            for code in selected:
                if code not in rows.index:
                    failed+=1
                    continue
                row=rows.loc[code]
                if row.volume<=0 or row.high==row.low or int(row.trading_status) in {2,3,5,6,8,9,11,12,14,15,16}:
                    failed+=1
                    continue
                held=holdings.get(code,{}).get('value',0)
                amount=max(0,min(target-held,cash/(1+cost_bps/10000)))
                quantity=math.floor(amount/float(row.close)/100)*100
                amount=quantity*float(row.close)
                if amount<=0:continue
                charge=amount*cost_bps/10000
                cash-=amount+charge
                holdings[code]={'value':held+amount,'price':float(row.close)}
                fees+=charge;turnover+=amount
                trades.append({'date':str(d.date()),'instrument_id':code,'side':'buy','quantity':quantity,'amount':amount,'fees':charge})
            pending=None
        nav.append({'date':str(d.date()),'nav':(cash+sum(p['value'] for p in holdings.values()))/initial,'cash':cash,'holdings':len(holdings)})
        if i%5==0 and d in signals:pending=signals[d]
    values=np.array([x['nav'] for x in nav])
    drawdown=values/np.maximum.accumulate(values)-1 if len(values) else np.array([0.])
    returns=np.diff(values)/values[:-1] if len(values)>1 else np.array([0.])
    for x,dd in zip(nav,drawdown):x['drawdown']=float(dd)
    return {'nav':nav,'trades':trades,'total_return':float(values[-1]-1) if len(values) else 0,
            'max_drawdown':float(drawdown.min()),'volatility':float(returns.std()*np.sqrt(244)),
            'fees':fees,'turnover':turnover/initial,'unfilled':failed,'open_positions':len(holdings),
            'assumptions':['次日收盘执行，每五个交易日调仓；买入按100股取整。',
                '持仓按含分红日收益漂移，减仓以金额计；属于研究模拟。',
                '单边综合成本由用户设定；不是逐日税费清单。',
                '一字价格日保守阻止交易；无行情持仓保留末值，不利用未来退市日强平。',
                '未成交卖单保留持仓，在下次调仓重新处理；期末不假设全部变现。']}
