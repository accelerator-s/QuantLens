"""Warehouse aggregation with label maturity enforced before data leaves Spark."""
from __future__ import annotations


def extract_daily(spark, profile, start, end, horizons):
    from pyspark.sql import functions as F
    from quantlens.signals import composite
    from quantlens.spark_factor_eval import daily_metrics

    scores, upstream = composite(spark, profile, start, end)
    labels = (spark.table('dwd.label_forward')
              .where((F.col('label_version') == 'v1') & F.col('valid')
                     & F.col('horizon').isin(horizons)
                     & F.col('date').between(start, end)
                     & (F.col('valid_until') <= F.lit(end))
                     & (F.col('valid_until') > F.col('date')))
              .select('date', 'code', 'horizon', 'ret', 'valid_until'))
    universe = (spark.table('dwd.universe').where('in_universe')
                .where(F.col('date').between(start, end)).select('date', 'code'))
    counts = universe.groupBy('date').agg(F.count('*').alias('_universe_n'))
    frame = (scores.join(labels, ['date', 'code']).join(universe, ['date', 'code'])
             .withColumnRenamed('score', 'std_value')
             .withColumn('factor', F.lit('research_signal')).withColumn('version', F.lit('exact_profile')))
    schema = ('factor string, version string, date date, horizon int, n long, rank_ic double, '
              'coverage double, q1_ret double, q2_ret double, q3_ret double, q4_ret double, '
              'q5_ret double, monotonicity double, _members array<string>')
    daily = frame.groupBy('date', 'horizon').applyInPandas(daily_metrics, schema)
    maturity = labels.groupBy('date', 'horizon').agg(F.max('valid_until').alias('label_max_end'))
    daily = (daily.drop('_members', 'factor', 'version').join(counts, 'date')
             .withColumn('coverage', F.col('n') / F.col('_universe_n')).drop('_universe_n')
             .join(maturity, ['date', 'horizon']))
    return daily, upstream
