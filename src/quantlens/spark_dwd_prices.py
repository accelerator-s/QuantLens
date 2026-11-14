from __future__ import annotations
from quantlens.warehouse import job, lineage, publish


def deduplicate(frame, keys):
    from pyspark.sql import functions as F, Window
    order = [F.col(c).desc_nulls_last() for c in ['updated_at', 'downloaded_at'] if c in frame.columns]
    order.append(F.sha2(F.to_json(F.struct(*sorted(frame.columns))), 256).desc())
    return frame.withColumn('_rn', F.row_number().over(Window.partitionBy(*keys).orderBy(*order))).where('_rn=1').drop('_rn')


def clean_prices(prices, returns, calendar=None, first_dates=None):
    from pyspark.sql import functions as F, Window
    prices = deduplicate(prices, ['instrument_id', 'date']).drop('year')
    returns = deduplicate(returns, ['instrument_id', 'date']).drop('year')
    shared = (set(prices.columns) & set(returns.columns))-{'instrument_id', 'date'}
    returns = returns.drop(*shared)
    out = prices.join(returns, ['instrument_id', 'date'], 'left').withColumnRenamed('instrument_id', 'code').withColumn('date', F.to_date('date'))
    def col(name, fallback):
        return F.col(name) if name in out.columns else fallback
    if calendar is None:
        calendar=out.select('date').distinct().withColumn('_day',F.row_number().over(Window.orderBy('date')))
    if first_dates is None:
        first_dates=out.groupBy('code').agg(F.min('date').alias('date')).join(calendar,'date').select('code',F.col('_day').alias('_first_day'))
    # Source returns are already adjustment-aware. Only derive returns when absent.
    if 'ret_wd' not in out.columns:
        adjustments=out.select('code','date',col('adj_close_wd',F.col('close')).alias('_adjusted'))
        adjustments=adjustments.withColumn('ret_wd',F.try_divide(F.col('_adjusted'),F.lag('_adjusted').over(Window.partitionBy('code').orderBy('date')))-1).select('code','date','ret_wd')
        out=out.join(adjustments,['code','date'])
    out=out.join(F.broadcast(calendar),'date').join(F.broadcast(first_dates),'code')
    out = (out.withColumn('adj_close', col('adj_close_wd', F.col('close')))
           .withColumn('total_mv', col('mktcap_total', F.lit(None).cast('double')))
           .withColumn('circ_mv', col('mktcap_float', F.lit(None).cast('double')))
           .withColumn('turnover_rate', col('turnover_rate', F.lit(None).cast('double')))
           .withColumn('listed_days', F.col('_day')-F.col('_first_day')+1)
           .withColumn('is_trading', (F.col('volume')>0)&(F.col('amount')>0))
           .withColumn('is_st', col('trading_status', F.lit(None)).cast('string').isin('2','3','5','6','8','9','11','12','14','15','16'))
           .withColumn('is_delisting', col('trading_status', F.lit(None)).cast('string').isin('6','9'))
           .withColumn('is_suspended', ~F.col('is_trading'))
           .withColumn('limit_up', (F.col('high')==F.col('low'))&(F.col('ret_wd')>0.09))
           .withColumn('limit_down', (F.col('high')==F.col('low'))&(F.col('ret_wd')< -0.09))
           .withColumn('ohlc_valid', (~F.col('is_trading')) | ((F.col('low')>0)&(F.col('low')<=F.least('open','close'))&(F.col('high')>=F.greatest('open','close'))))
           .withColumn('year', F.year('date')))
    return out.drop('_day','_first_day')


def universe(prices):
    from pyspark.sql import functions as F
    from quantlens.security_master import A_SHARE_PATTERN
    reasons = F.concat_ws(',', F.when(~F.col('code').rlike(A_SHARE_PATTERN), 'not_a_share'),
        F.when(F.col('code').rlike('^(43|83|87|88|92)') & (F.col('date')<F.lit('2021-11-15')), 'before_bse_listing'),
        F.when(F.col('listed_days')<120, 'new_listing'),
        F.when(F.coalesce('is_st', F.lit(True)), 'st_or_unknown'), F.when(F.coalesce('is_delisting', F.lit(True)), 'delisting_or_unknown'),
        F.when(~F.coalesce('is_trading',F.lit(False)), 'not_trading'), F.when(F.coalesce(F.col('amount')<5000000,F.lit(True)), 'low_amount'),
        F.when(F.coalesce(F.col('close')<1,F.lit(True)), 'low_price'), F.when(~F.coalesce('ohlc_valid',F.lit(False)), 'invalid_ohlc'))
    return prices.select('code','date','year', reasons.alias('exclusion_reason')).withColumn('in_universe', F.col('exclusion_reason')=='')


def run(spark, context):
    from quantlens.stage_cache import StageCache
    from quantlens.spark_quality import prices_gate
    from pyspark.sql import functions as F, Window
    source=spark.table('ods.prices_daily')
    calendar=(source.select(F.to_date('date').alias('date')).distinct()
              .withColumn('_day',F.row_number().over(Window.orderBy('date')))).cache()
    first_dates=(source.groupBy('instrument_id').agg(F.min(F.to_date('date')).alias('date')).join(calendar,'date')
                 .select(F.col('instrument_id').alias('code'),F.col('_day').alias('_first_day'))).cache()
    calendar.count();first_dates.count()
    try:
        cache = StageCache(spark, context)
        # Bound shuffle/cache size by ODS year; listing age uses the global calendar.
        for row in source.select('year').distinct().orderBy('year').collect():
            year=row.year
            def clean_year():
                prices=clean_prices(source.where(F.col('year')==year),spark.table('ods.returns_daily').where(F.col('year')==year),calendar,first_dates)
                publish(spark,lineage(prices,context),'dwd.prices_clean',['year'],run=context,quality=prices_gate)
                published=spark.table('dwd.prices_clean').where(F.col('year')==year)
                publish(spark,lineage(universe(published),context),'dwd.universe',['year'],run=context)
            reused = cache.run('dwd_prices_year', clean_year, year=year)
            print(f'QUANTLENS_PRICES_YEAR_{"REUSED" if reused else "CLEANED"}={year}',flush=True)
    finally:
        calendar.unpersist();first_dates.unpersist()

if __name__ == '__main__':
    with job('dwd_prices') as (spark, context): run(spark, context)
