from __future__ import annotations
from quantlens.warehouse import root_uri, write_json, lineage, publish, batch_identifier

MIRRORS={'factor_eval_daily':'dws.factor_eval_daily','model_comparison':'ads.model_comparison','factor_meta':'dws.factor_meta','factor_eval':'dws.factor_eval','factor_values':'dws.factor_values','factor_weights':'dws.factor_weights','strategy_signal':'ads.strategy_signal','backtest_summary':'ads.backtest_summary','backtest_nav':'ads.backtest_nav','backtest_trades':'ads.backtest_trades','backtest_positions':'ads.backtest_positions','attribution':'ads.attribution','portfolio_target':'ads.portfolio_target','weighting_compare':'ads.weighting_compare','audit_log':'ads.audit_log','quality_report':'dwd.quality_report'}


def recent_events(frame,keys):
    from pyspark.sql import functions as F, Window
    window=Window.partitionBy(*keys).orderBy(F.col('effective_date').desc())
    return frame.withColumn('_recent',F.row_number().over(window)).where('_recent<=12').drop('_recent')


def visible_events(frame,keys,start,end):
    from pyspark.sql import functions as F,Window
    frame=frame.where(F.col('effective_date')<=F.lit(end))
    prior=(frame.where(F.col('effective_date')<F.lit(start))
           .withColumn('_prior',F.row_number().over(Window.partitionBy(*keys).orderBy(F.col('effective_date').desc())))
           .where('_prior=1').drop('_prior'))
    return frame.where(F.col('effective_date')>=F.lit(start)).unionByName(prior)

def build(spark,context):
    from pyspark.sql import functions as F, Window
    from quantlens.panel_source import fields
    all_prices=spark.table('dwd.prices_clean')
    as_of=all_prices.agg(F.max('date').alias('date')).first().date
    prices=all_prices.where(F.col('date')>=F.date_sub(F.lit(as_of),550))
    universe=spark.table('dwd.universe').select('code','date','in_universe')
    w=Window.partitionBy('code').orderBy('date');w60=w.rowsBetween(-59,0)
    prices=(prices.withColumn('momentum_20',F.try_divide(F.col('adj_close'),F.lag('adj_close',20).over(w))-1)
            .withColumn('position_60',F.try_divide(F.col('adj_close')-F.min('adj_close').over(w60),F.max('adj_close').over(w60)-F.min('adj_close').over(w60)))
            .withColumn('ma60',F.avg('close').over(w60))).where(F.col('date')>=F.date_sub(F.lit(as_of),400))
    market=(prices.join(universe,['code','date']).groupBy('date').agg(F.count('*').alias('stocks'),F.avg('ret_wd').alias('equal_weight_return'),F.percentile_approx('ret_wd',.5).alias('median_return'),F.sum((F.col('ret_wd')>0).cast('long')).alias('advancing'),F.sum((F.col('ret_wd')<0).cast('long')).alias('declining'),F.avg(F.when(F.col('in_universe'),(F.col('close')>F.col('ma60')).cast('double'))).alias('breadth')))
    overview=all_prices.agg(F.min('date').alias('start'),F.max('date').alias('end'),F.count('*').alias('rows'),F.countDistinct('code').alias('instruments'),F.countDistinct('date').alias('days'))
    overview=overview.withColumn('factor_evaluations',F.lit(spark.table('dws.factor_eval').count() if spark.catalog.tableExists('dws.factor_eval') else 0))
    from datetime import timedelta
    serving_start=as_of-timedelta(days=400)
    derived=fields(spark,['roe_ttm','total_liabilities','total_assets','industry_code','industry_name'],serving_start,as_of)
    derived=derived.withColumn('debt_ratio',F.try_divide(F.col('total_liabilities'),F.col('total_assets')))
    screen=prices.join(universe,['code','date']).join(derived,['code','date'],'left')
    tables={'overview':overview,'market':market,'stock_prices':prices,'stock_fundamentals':recent_events(spark.table('dwd.fundamentals_pit'),['code','statement','item']),'stock_analyst':visible_events(spark.table('dwd.analyst_pit'),['code','item'],serving_start,as_of),'stock_industry':recent_events(spark.table('dwd.industry_pit'),['code','classification_id']),'screen':screen}
    for table,frame in tables.items():
        publish(spark,lineage(frame,context).withColumn('as_of',F.lit(as_of)),'ads.'+table,['as_of'],run=context)
    return as_of


def export(spark,context,batch_id,*,data_only=False):
    from pyspark.sql import functions as F
    batch_identifier(batch_id)
    as_of=build(spark,context)
    from quantlens.archive_ads import run as archive
    if not data_only:archive(spark,context)
    tables={**({'quality_report':MIRRORS['quality_report']} if data_only else MIRRORS),**{name:'ads.'+name for name in ['overview','market','stock_prices','stock_fundamentals','stock_analyst','stock_industry','screen']}}
    manifest={'data_as_of':str(as_of),'run_id':context['run_id'],'data_snapshot':context['data_snapshot'],'tables':{}}
    if data_only:manifest['scope']='data'
    for name,source in tables.items():
        if not spark.catalog.tableExists(source): continue
        frame=spark.table(source)
        if name in {'overview','market','stock_prices','stock_fundamentals','stock_analyst','stock_industry','screen'}:
            frame=frame.where(F.date_format('as_of','yyyy-MM-dd')==str(as_of))
        elif source.startswith('ads.') and 'as_of' in frame.columns:
            # Hive 3/PG cannot bind DATE partition predicates correctly. This
            # explicit expression filters the small partition list in Spark.
            frame=frame.where(F.date_format('as_of','yyyy-MM-dd')>=F.date_format(F.add_months(F.current_date(),-36),'yyyy-MM-dd'))
        if name in {'factor_values','stock_prices'}:
            # Keep the serving extraction bounded; the full research history stays in DWD/DWS.
            frame=frame.where(F.col('date')>=F.date_sub(F.lit(as_of),60 if name=='factor_values' else 400))
        uri=f'{root_uri()}/ads/_exports/{batch_id}/{name}'
        frame.coalesce(1 if name not in {'factor_values','stock_prices'} else 8).write.mode('errorifexists').option('compression','snappy').parquet(uri)
        manifest['tables'][name]={'source_uri':uri,'data_as_of':str(as_of),'run_id':context['run_id'],'data_snapshot':context['data_snapshot'],'rows':frame.count()}
    write_json(spark,f'{root_uri()}/meta/batches/{batch_id}/manifest',manifest)
    return manifest


if __name__=='__main__':
    import argparse
    from quantlens.warehouse import job
    parser=argparse.ArgumentParser()
    parser.add_argument('--batch',required=True)
    parser.add_argument('--data-only',action='store_true')
    args=parser.parse_args()
    with job('publish_ads') as (spark,context):
        export(spark,context,args.batch,data_only=args.data_only)
