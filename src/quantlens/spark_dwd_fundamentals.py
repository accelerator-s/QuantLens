from __future__ import annotations
from quantlens.warehouse import job, lineage, publish
from quantlens.spark_dwd_prices import deduplicate


def long_pit(frame, statement, *, numeric=None, date_column='effective_date'):
    from pyspark.sql import functions as F
    numeric = numeric or [f.name for f in frame.schema if f.dataType.simpleString() in {'double','float','bigint','int'} and f.name not in {'year'}]
    ann = F.to_date('ann_date') if 'ann_date' in frame.columns else F.to_date(date_column)
    effective = F.to_date(date_column)
    report = F.to_date('report_period') if 'report_period' in frame.columns else F.lit(None).cast('date')
    records = F.array(*[F.struct(F.lit(name).alias('item'), F.col(name).cast('double').alias('value')) for name in numeric])
    out = frame.select(F.col('instrument_id').alias('code'), ann.alias('ann_date'), report.alias('report_period'), effective.alias('effective_date'), F.explode(records).alias('_item'))
    return (out.select('code','ann_date','report_period','effective_date', F.lit(statement).alias('statement'),'_item.*')
            .where('effective_date IS NOT NULL AND ann_date IS NOT NULL')
            .withColumn('year', F.year('effective_date')))


def run(spark, context):
    from pyspark.sql import functions as F
    from quantlens.spark_quality import pit_gate
    from quantlens.spark_derived import disclosed,fundamental_ttm,holder_longline
    frames = []
    for table in ['fs_balance','fs_income','fs_cashflow']:
        frame = disclosed(spark.table('ods.'+table).drop('year'))
        frames.append(long_pit(frame, table))
    frames.append(long_pit(fundamental_ttm(spark.table('ods.fs_income'),spark.table('ods.fs_balance')),'fundamental_ttm'))
    frames.append(long_pit(holder_longline(spark.table('ods.top_float_shareholders')),'holder_longline'))
    frame = frames[0]
    for other in frames[1:]: frame = frame.unionByName(other)
    frame = deduplicate(frame, ['code','effective_date','report_period','statement','item'])
    publish(spark, lineage(frame, context), 'dwd.fundamentals_pit', ['year'], run=context, quality=pit_gate)

if __name__ == '__main__':
    with job('dwd_fundamentals') as (spark, context): run(spark, context)
