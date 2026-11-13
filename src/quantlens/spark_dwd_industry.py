from quantlens.warehouse import job, lineage, publish
from quantlens.spark_dwd_prices import deduplicate


def run(spark, context):
    from pyspark.sql import functions as F
    source = spark.table('ods.industry_classification')
    frame = source.select(F.col('instrument_id').alias('code'), F.to_date('effective_date').alias('effective_date'),
                          'classification_id','industry_code','industry_name')
    frame = deduplicate(frame, ['code','classification_id','effective_date']).where('effective_date IS NOT NULL').withColumn('year', F.year('effective_date'))
    publish(spark, lineage(frame, context), 'dwd.industry_pit', ['year'], run=context)

if __name__ == '__main__':
    with job('dwd_industry') as (spark, context): run(spark, context)
