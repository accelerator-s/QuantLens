from quantlens.warehouse import job, lineage, publish


def run(spark,context):
    from pyspark.sql import functions as F, Window
    from pyspark.ml.fpm import FPGrowth
    from factors.registry import FactorRegistry
    metadata=[(m.name,m.version,-1. if m.direction=='negative' else 1.) for m in FactorRegistry().list() if m.name!='model_gbt']
    current=spark.createDataFrame(metadata,'factor string, version string, _direction double')
    values=spark.table('dws.factor_values').join(F.broadcast(current),['factor','version']).withColumn('_signal',F.col('std_value')*F.col('_direction'))
    monthly=values.withColumn('_month',F.date_format('date','yyyy-MM'))
    dates=monthly.groupBy('_month').agg(F.max('date').alias('date'))
    monthly=monthly.join(dates,['_month','date'])
    ranked=monthly.withColumn('_q',F.percent_rank().over(Window.partitionBy('date','factor','version').orderBy('_signal')))
    # Include empty transactions so support denominator is all monthly stock records.
    transactions=ranked.groupBy('date','code').agg(F.collect_set(F.when(F.col('_q')>=.8,F.concat_ws('@','factor','version'))).alias('items'))
    model=FPGrowth(itemsCol='items',minSupport=.05,minConfidence=.6).fit(transactions)
    count=transactions.count()
    itemsets=model.freqItemsets.withColumn('support',F.col('freq')/F.lit(count)).withColumn('kind',F.lit('itemset')).select(F.to_json('items').alias('items'),'support',F.lit(None).cast('double').alias('confidence'),F.lit(None).cast('double').alias('lift'),'kind')
    rules=model.associationRules.select(F.to_json(F.struct('antecedent','consequent')).alias('items'),'support','confidence','lift',F.lit('rule').alias('kind'))
    frame=itemsets.unionByName(rules).withColumn('interpretation',F.lit('月度最高分位共现；仅供生成候选，需经统一评估和回测'))
    publish(spark,lineage(frame,context).withColumn('as_of',F.current_date()),'dws.factor_itemset',['as_of'],run=context)

if __name__=='__main__':
    with job('fpgrowth') as (spark,context): run(spark,context)
