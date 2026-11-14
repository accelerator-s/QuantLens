"""Broad market styles; classification never depends on passing an alpha screen."""
from __future__ import annotations

STYLE_METHOD_VERSION = 'market_rank_v2'
STYLE_FEATURES = ['size_rank', 'momentum60_rank', 'volatility60_rank', 'liquidity20_rank']


def market_style_features(prices):
    """Use only past/current observations and the complete current cross section.

    A missing feature receives a fixed neutral rank, not a statistic learned on
    future dates. `_observed_features` preserves the amount of actual evidence.
    Callers must bound prices by the research end before invoking this function.
    """
    from pyspark.sql import functions as F, Window

    def finite(column):
        return F.when(column.isNotNull() & ~F.isnan(column)
                      & (F.abs(column) < float('inf')), column)

    history = Window.partitionBy('code').orderBy('date')
    w60 = history.rowsBetween(-59, 0)
    w20 = history.rowsBetween(-19, 0)
    frame = (prices.withColumn('_adj', F.when(F.col('adj_close') > 0, F.col('adj_close')))
        .withColumn('_ret', finite(F.col('ret_wd')))
        .withColumn('_amount', F.when(F.col('amount') > 0, finite(F.col('amount'))))
        .withColumn('_previous60', F.lag('_adj', 60).over(history)))
    frame = (frame.withColumn('_size', F.when(F.col('total_mv') > 0, finite(F.col('total_mv'))))
        .withColumn('_momentum', finite(F.try_divide('_adj', '_previous60') - 1))
        .withColumn('_volatility', F.when(F.count('_ret').over(w60) >= 40,
                                         F.stddev_samp('_ret').over(w60)))
        .withColumn('_liquidity', F.avg('_amount').over(w20))
        .where((F.col('listed_days') >= 120) & (F.col('close') > 0)))
    raw = ['_size', '_momentum', '_volatility', '_liquidity']
    frame = frame.withColumn('_observed_features', sum(F.col(name).isNotNull().cast('int') for name in raw))
    day = Window.partitionBy('date')
    for source, target in zip(raw, STYLE_FEATURES):
        order = day.orderBy(F.col(source).asc_nulls_last())
        ties = Window.partitionBy('date', source)
        average_rank = F.rank().over(order) + (F.count('*').over(ties) - 1) / 2
        value = F.when(F.col(source).isNotNull(),
                       F.try_divide(average_rank, F.count(source).over(day)))
        frame = frame.withColumn(target, F.coalesce(value, F.lit(.5)))
    return frame.select('code', 'date', *STYLE_FEATURES, '_observed_features')


def market_source(spark, *, start=None, end=None):
    from pyspark.sql import functions as F

    prices = spark.table('dwd.prices_clean')
    if end:
        prices = prices.where(F.col('date') <= end)
    if start:
        from datetime import date, timedelta
        lower = (date.fromisoformat(start) - timedelta(days=400)).isoformat()
        prices = prices.where(F.col('date') >= lower)
    frame = market_style_features(prices.select('code', 'date', 'adj_close', 'ret_wd',
                                               'amount', 'total_mv', 'listed_days', 'close'))
    if start:
        frame = frame.where(F.col('date') >= start)
    return frame


def carry_prediction_features(observations):
    """Classify existing holdings on quote gaps using only last visible features.

    This panel is for prediction only. Synthetic quote-gap rows must never enter
    KMeans fitting or the investable universe. On a year boundary the new model
    reclassifies the last feature vector, rather than carrying an old cluster ID.
    """
    from pyspark.sql import functions as F, Window

    dates = observations.select('date').distinct()
    first = observations.groupBy('code').agg(F.min('date').alias('_first'))
    calendar = dates.crossJoin(first).where(F.col('date') >= F.col('_first')).drop('_first')
    visible = observations.select('code', 'date', F.struct(
        *[F.col(name) for name in STYLE_FEATURES],
        F.col('_observed_features'), F.col('date').alias('feature_asof')).alias('_observation'))
    past = Window.partitionBy('code').orderBy('date').rowsBetween(Window.unboundedPreceding, 0)
    result = calendar.join(visible, ['code', 'date'], 'left').withColumn(
        '_latest', F.last('_observation', ignorenulls=True).over(past))
    return result.select('code', 'date', *[F.col('_latest.' + name).alias(name)
        for name in [*STYLE_FEATURES, '_observed_features', 'feature_asof']])
