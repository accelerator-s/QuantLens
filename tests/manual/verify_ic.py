"""Independently check published Rank IC against Pandas on canonical labels."""
import json
import numpy as np
import pandas as pd
from pyspark.sql import functions as F
from factors.registry import FactorRegistry
from quantlens.warehouse import job
with job('ic_consistency') as (spark,context):
    name='reversal_5';version=FactorRegistry().get(name).version
    latest=spark.table('dws.factor_eval_daily').where((F.col('factor')==name)&(F.col('version')==version)&(F.col('horizon')==5)).orderBy(F.col('date').desc()).limit(30)
    expected=latest.select('date','rank_ic').toPandas();expected.date=pd.to_datetime(expected.date)
    assert len(expected),'No published daily IC'
    first=str(expected.date.min())[:10];last=str(expected.date.max())[:10]
    values=spark.table('dws.factor_values').where((F.col('factor')==name)&(F.col('version')==version)&(F.col('date')>=first)&(F.col('date')<=last)).select('code','date','std_value')
    labels=spark.table('dwd.label_forward').where("horizon=5 AND label_version='v1' AND valid").select('code','date','ret')
    universe=spark.table('dwd.universe').where('in_universe').select('code','date')
    pdf=values.join(labels,['code','date']).join(universe,['code','date']).toPandas();pdf.date=pd.to_datetime(pdf.date)
    rows=[]
    for dt,group in pdf.groupby('date'):
        valid=group[['std_value','ret']].replace([np.inf,-np.inf],np.nan).dropna()
        actual=float(valid.rank(method='average').corr().iloc[0,1])
        stored=float(expected.loc[expected.date==dt,'rank_ic'].iloc[0])
        rows.append({'date':str(dt.date()),'pandas_ic':actual,'spark_ic':stored,'absolute_error':abs(actual-stored),'n':len(valid)})
    error=max(row['absolute_error'] for row in rows)
    assert error<=.002,error
    print('QUANTLENS_IC_CONSISTENCY='+json.dumps({'factor':name,'version':version,'days':rows,'max_absolute_error':error,**context}),flush=True)
