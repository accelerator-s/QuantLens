"""Compare 200 stocks against full-cross-section Pandas formulas on canonical DWD."""
import gc,json
import numpy as np
import pandas as pd
from factors.registry import FactorRegistry
from quantlens.panel_source import fields
from quantlens.warehouse import job
from paths import workspace
from pyspark.sql import functions as F

with job('factor_consistency') as (spark,context):
    calendar=[row.date for row in spark.table('dwd.prices_clean').select('date').distinct().orderBy('date').collect()]
    output=calendar[-10:];checks=[]
    for meta in FactorRegistry().list():
        if meta.engine!='pandas':continue
        history=calendar[max(0,len(calendar)-10-meta.min_history)]
        pdf=fields(spark,meta.dependencies,history,calendar[-1]).toPandas()
        pdf.date=pd.to_datetime(pdf.date)
        panel={name:pdf.pivot(index='date',columns='code',values=name).sort_index() for name in meta.dependencies}
        namespace={};exec((workspace().root/meta.plugin_path).read_text(),namespace)
        expected=namespace['compute'](panel)
        for field,minimum in meta.eligibility.items():expected=expected.where(panel[field]>=minimum)
        values=spark.table('dws.factor_values').where((F.col('factor')==meta.name)&(F.col('version')==meta.version))
        eligible=spark.table('dwd.universe').where((F.col('date')==calendar[-1])&F.col('in_universe')).select('code','date')
        codes=[row.code for row in values.where((F.col('date')==calendar[-1])&F.col('value').isNotNull()).join(eligible,['code','date']).select('code').distinct().orderBy('code').limit(200).collect()]
        assert len(codes)==200,(meta.name,len(codes))
        actual=values.where((F.col('date')>=output[0])&(F.col('date')<=output[-1])&F.col('code').isin(codes)).select('code','date','value').toPandas()
        actual.date=pd.to_datetime(actual.date)
        actual=actual.pivot(index='date',columns='code',values='value').reindex(index=pd.to_datetime(output),columns=codes)
        expected=expected.reindex(index=actual.index,columns=codes).round(6)
        a=actual.to_numpy(dtype=float,na_value=np.nan);b=expected.to_numpy(dtype=float,na_value=np.nan)
        np.testing.assert_array_equal(np.isnan(a),np.isnan(b),err_msg=meta.name)
        valid=np.isfinite(a)&np.isfinite(b)
        error=float(np.max(np.abs(a[valid]-b[valid]))) if valid.any() else 0.
        variable=bool(valid.any() and np.std(a[valid])>0 and np.std(b[valid])>0)
        correlation=float(np.corrcoef(a[valid],b[valid])[0,1]) if variable else None
        assert error<=1e-6,(meta.name,error)
        assert correlation is None or correlation>=.999,(meta.name,correlation)
        checks.append({'factor':meta.name,'version':meta.version,'stocks':len(codes),'pairs':int(valid.sum()),'max_absolute_error':error,'pearson':correlation,'constant_equal':not variable and error==0})
        print('QUANTLENS_FACTOR_CHECK='+json.dumps(checks[-1]),flush=True)
        del pdf,panel,expected,actual,a,b,namespace
        gc.collect();spark.catalog.clearCache();spark._jvm.System.gc()
    print('QUANTLENS_FACTOR_CONSISTENCY='+json.dumps({'checks':checks,'output_dates':[str(day) for day in output],'method':'Pandas computes the full market cross section; only comparison selects 200 stocks.',**context}),flush=True)
