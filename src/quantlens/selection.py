import duckdb
import numpy as np

from exceptions import ContractViolation
from quantlens.spark_features import FEATURES,LABELS
from quantlens import store

def select(root,config,reporter):
    with duckdb.connect() as db:
        db.execute("SET threads=4")
        db.execute("SET memory_limit='3GB'")
        db.read_parquet(str(root/'features'/'*.parquet')).create_view('features')
        db.execute('CREATE TEMP TABLE train AS SELECT * FROM features WHERE eligible AND date<=? AND label_end<=? AND label IS NOT NULL',
                   [config.train_end,config.train_end])
        scores=[]
        for feature,label in zip(FEATURES,LABELS):
            rows=db.execute(f'''WITH ranks AS (SELECT date,
                rank() OVER(PARTITION BY date ORDER BY {feature})+(count(*) OVER(PARTITION BY date,{feature})-1)/2. AS x,
                rank() OVER(PARTITION BY date ORDER BY label)+(count(*) OVER(PARTITION BY date,label)-1)/2. AS y
                FROM train WHERE isfinite({feature})),
                daily AS (SELECT date,corr(x,y) ic FROM ranks GROUP BY date),
                quarters AS (SELECT date_trunc('quarter',date) period,avg(ic) ic FROM daily GROUP BY 1)
                SELECT (SELECT avg(ic) FROM daily) mean_ic,(SELECT stddev_samp(ic) FROM daily) ic_std,
                count(ic) periods,avg(CAST(ic>0 AS DOUBLE)) positive_periods FROM quarters''').fetchone()
            ic,std,n,pos=rows
            stable=max(pos,1-pos) if pos is not None else 0
            scores.append({'feature':feature,'label':label,'rank_ic':ic,'ic_std':std,
                           'periods':n,'sign_stability':stable,'selected':False,'reason':''})
        ordered=sorted(scores,key=lambda r:abs(r['rank_ic'] or 0),reverse=True)
        chosen=[]
        for row in ordered:
            f=row['feature']
            if row['rank_ic'] is None or abs(row['rank_ic'])<0.005 or row['sign_stability']<0.6:
                row['reason']='训练期相关不足或方向不稳';continue
            correlations=[db.execute(f'SELECT corr({f},{g}) FROM train WHERE isfinite({f}) AND isfinite({g})').fetchone()[0] for g in chosen]
            match=next((g for g,c in zip(chosen,correlations) if c is not None and abs(c)>0.95),None)
            if match:
                row['reason']=f'与 {match} 高度相关';continue
            row['selected']=True;row['reason']='通过训练期筛选';chosen.append(f)
    if len(chosen)<2:
        raise ContractViolation(code='QL_FACTORS_INSUFFICIENT',message='训练期有效因子不足两项',suggested_action='扩大训练窗口，检查数据质量')
    result={'selected':chosen,'scores':scores,'scope':'train','min_abs_ic':0.005,'min_sign_stability':0.6,'max_correlation':0.95,
            'note':'先固定规则，再按训练期日IC、季度方向和两两相关性筛选。'}
    store.write_json(root/'selection.json',result)
    reporter(f'训练期筛选：{len(FEATURES)} 项中保留 {len(chosen)} 项')
    return result
