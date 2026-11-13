from quantlens.warehouse import job, lineage, publish
from quantlens.spark_dwd_fundamentals import long_pit
from quantlens.spark_dwd_prices import deduplicate


def run(spark, context):
    from pyspark.sql import functions as F
    from quantlens.spark_quality import pit_gate
    from quantlens.spark_derived import consensus
    source = consensus(spark.table('ods.analyst_forecast'))
    frame = long_pit(source, 'consensus_daily', date_column='effective_date')
    frame = deduplicate(frame, ['code','effective_date','item'])
    publish(spark, lineage(frame, context), 'dwd.analyst_pit', ['year'], run=context, quality=pit_gate)

if __name__ == '__main__':
    with job('dwd_analyst') as (spark, context): run(spark, context)
