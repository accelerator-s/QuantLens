from __future__ import annotations
import pandas as pd
from backtest.signal import SignalResult, _resolve_direction
from quantlens.warehouse import session


def inputs(profile,start,end,spark=None):
    from pyspark.sql import functions as F, Window
    from factors.registry import FactorRegistry
    owned=spark is None
    spark=spark or session('backtest_inputs')
    try:
        prices=spark.table('dwd.prices_clean').where((F.col('date')<=end)&(F.col('date')>=str(pd.Timestamp(start)-pd.Timedelta(days=150))[:10]))
        dates=prices.where(F.col('date')>=start).select('date').distinct().orderBy('date').toPandas()['date']
        index=pd.DatetimeIndex(pd.to_datetime(dates))
        market_pdf=prices.where(F.col('date')>=str(pd.Timestamp(start)-pd.Timedelta(days=100))[:10]).select('code','date','close','high','low','is_trading','listed_days','trading_status','ret_wd','is_delisting','circ_mv').toPandas()
        market_pdf.date=pd.to_datetime(market_pdf.date)
        market={col:market_pdf.pivot(index='date',columns='code',values=col).sort_index() for col in market_pdf.columns if col not in {'code','date'}}
        columns=market['close'].columns
        eligible=spark.table('dwd.universe').where((F.col('date')>=str(market_pdf.date.min())[:10])&(F.col('date')<=end)).select('code','date','in_universe').toPandas()
        eligible.date=pd.to_datetime(eligible.date)
        mask=eligible.pivot(index='date',columns='code',values='in_universe').reindex(index=market['close'].index,columns=columns).fillna(False)
        from quantlens.signals import composite
        scored,metadata=composite(spark,profile,start,end)
        signal_frame=scored.toPandas();signal_frame.date=pd.to_datetime(signal_frame.date)
        scores=signal_frame.pivot(index='date',columns='code',values='score').reindex(index=index,columns=columns)
        hashes={name:','.join(meta['code_hashes']) for name,meta in metadata.items()}
        directions={name:meta['direction'] for name,meta in metadata.items()}
        signal=SignalResult(scores=scores,factor_code_hashes=hashes,directions=directions,
                            notes=['DWS 标准化值、声明方向和已成熟训练窗口的权重'],
                            provenance={'factors':metadata,'data_snapshot':sorted({s for m in metadata.values() for s in m['data_snapshots']}),'label_version':'v1'})
        clusters=spark.table('dws.style_cluster').where((F.col('date')>=str(market_pdf.date.min())[:10])&(F.col('date')<=end)).select('code','date','cluster_id').toPandas() if spark.catalog.tableExists('dws.style_cluster') else pd.DataFrame()
        if not clusters.empty:
            clusters.date=pd.to_datetime(clusters.date)
            clusters=clusters.pivot(index='date',columns='code',values='cluster_id').reindex(index=market['close'].index,columns=columns)
        else: clusters=pd.DataFrame(index=market['close'].index,columns=columns)
        # Industry values are resolved PIT by the same panel source used for factors.
        from quantlens.panel_source import fields
        industries=fields(spark,['industry_code'],str(market_pdf.date.min())[:10],end).toPandas()
        industries.date=pd.to_datetime(industries.date)
        industries=industries.pivot(index='date',columns='code',values='industry_code').reindex(index=market['close'].index,columns=columns)
        ma_window=Window.partitionBy('code').orderBy('date').rowsBetween(-59,0)
        breadth=prices.withColumn('_ma',F.avg('close').over(ma_window)).join(spark.table('dwd.universe').select('code','date','in_universe'),['code','date']).where(F.col('in_universe')&(F.col('date')>=start)).groupBy('date').agg(F.avg((F.col('close')>F.col('_ma')).cast('double')).alias('breadth')).toPandas()
        breadth.date=pd.to_datetime(breadth.date)
        return {'signal':signal,'market':market,'mask':mask,'clusters':clusters,'industries':industries,'breadth':breadth.set_index('date').breadth}
    finally:
        if owned: spark.stop()
