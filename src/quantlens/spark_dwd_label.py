from __future__ import annotations
from quantlens.warehouse import job, lineage, publish


def forward_labels(prices, universe, horizons=(1,5,10,20), label_version='v1'):
    from pyspark.sql import functions as F, Window
    # Global trading calendar prevents suspended stocks from shifting the horizon.
    calendar = prices.select('date').distinct().withColumn('_day', F.row_number().over(Window.orderBy('date')))
    dated = prices.select('code','date','adj_close').join(calendar, 'date')
    entries = dated.select('code', (F.col('_day')-1).alias('_day'), F.col('adj_close').alias('_entry'))
    frames = []
    for horizon in horizons:
        exits = dated.select('code', (F.col('_day')-horizon-1).alias('_day'), F.col('adj_close').alias('_exit'), F.col('date').alias('valid_until'))
        frame = dated.join(entries, ['code','_day'], 'left').join(exits, ['code','_day'], 'left')
        expected_exit = calendar.select((F.col('_day')-horizon-1).alias('_day'),F.col('date').alias('_expected_exit'))
        frame = frame.join(expected_exit, '_day', 'left').drop('valid_until').withColumnRenamed('_expected_exit','valid_until')
        frame = frame.withColumn('ret', F.when((F.col('_entry')>0)&F.col('_exit').isNotNull(), F.col('_exit')/F.col('_entry')-1))
        eligible = universe.select('code','date','in_universe')
        frame = frame.join(eligible, ['code','date'], 'left')
        benchmark = frame.where('in_universe AND ret IS NOT NULL').groupBy('date').agg(F.avg('ret').alias('bench_ret'))
        frames.append(frame.join(benchmark, 'date', 'left').select('code','date',F.lit(horizon).alias('horizon'),'ret','bench_ret',
                (F.col('ret').isNotNull()&F.col('valid_until').isNotNull()).alias('valid'),'valid_until', F.lit(label_version).alias('label_version'),F.year('date').alias('year')))
    result = frames[0]
    for other in frames[1:]: result = result.unionByName(other)
    return result


def run(spark, context):
    import yaml
    from datetime import date,timedelta
    from pyspark.sql import functions as F
    from paths import workspace
    from quantlens.spark_quality import labels_gate
    config = yaml.safe_load((workspace().config/'labels.yaml').read_text())
    prices=spark.table('dwd.prices_clean')
    universe=spark.table('dwd.universe')
    from quantlens.warehouse import root_uri,_fs
    staging=f'{root_uri()}/dwd/label_forward/_tmp/prepared-{context["run_id"]}'
    try:
        for row in prices.select('year').distinct().orderBy('year').collect():
            year=row.year
            # Include the next year's opening sessions so year-end exits stay global.
            bounds=prices.where((F.col('date')>=f'{year}-01-01')&(F.col('date')<=str(date(year,12,31)+timedelta(days=90))))
            frame=forward_labels(bounds,universe,config['horizons'],config['label_version']).where(F.col('year')==year)
            frame.coalesce(4).write.mode('append').partitionBy('year','horizon').parquet(staging)
            print(f'QUANTLENS_LABEL_YEAR={year}',flush=True)
        # The coverage contract is measured on the complete mature universe for
        # each horizon. Prepare in bounded years, validate before any pointer moves.
        frame=spark.read.parquet(staging)
        publish(spark,lineage(frame,context),'dwd.label_forward',['label_version','horizon','year'],run=context,
                quality=lambda f:labels_gate(f,universe))
    finally:
        fs,path=_fs(spark,staging)
        if fs.exists(path):fs.delete(path,True)

if __name__ == '__main__':
    with job('dwd_label') as (spark, context): run(spark, context)
