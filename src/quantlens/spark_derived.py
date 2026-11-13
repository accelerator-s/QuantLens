"""PIT source derivations executed by Spark before DWD long-table publication."""
from quantlens.spark_dwd_prices import deduplicate


def disclosed(frame):
    from pyspark.sql import functions as F
    if 'statement_type' in frame.columns:frame=frame.where("statement_type='consolidated'")
    if 'ann_date_imputed' in frame.columns:
        frame=frame.where(F.col('ann_date_imputed').isNull()|F.col('ann_date_imputed').cast('string').isin('false','False','0',''))
    return frame


def fundamental_ttm(income,balance):
    from pyspark.sql import functions as F,Window
    keys=['instrument_id','report_period','effective_date']
    income=deduplicate(disclosed(income),keys).select(*keys,'net_profit_parent','revenue','operating_cost')
    balance=deduplicate(disclosed(balance),keys).select(*keys,'equity_parent')
    base=income.alias('p')
    prior_annual=income.alias('a');prior_same=income.alias('s');equity=balance.alias('b')
    last_year=(F.substring('p.report_period',1,4).cast('int')-1).cast('string')
    base=base.join(prior_annual,(F.col('p.instrument_id')==F.col('a.instrument_id'))&(F.col('a.report_period')==F.concat(last_year,F.lit('-12-31'))),'left')
    base=base.join(prior_same,(F.col('p.instrument_id')==F.col('s.instrument_id'))&(F.col('s.report_period')==F.concat(last_year,F.substring('p.report_period',5,6))),'left')
    base=base.join(equity,(F.col('p.instrument_id')==F.col('b.instrument_id'))&(F.col('p.report_period')==F.col('b.report_period')),'left')
    annual=F.substring('p.report_period',6,5)=='12-31'
    def ttm(name):return F.when(annual,F.col('p.'+name)).otherwise(F.col('p.'+name)+F.col('a.'+name)-F.col('s.'+name))
    visible=F.greatest(F.to_date('p.effective_date'),F.to_date('b.effective_date'),F.when(~annual,F.to_date('a.effective_date')),F.when(~annual,F.to_date('s.effective_date')))
    profit=ttm('net_profit_parent');revenue=ttm('revenue');cost=ttm('operating_cost')
    frame=base.select(F.col('p.instrument_id').alias('instrument_id'),F.col('p.report_period').alias('report_period'),visible.alias('effective_date'),
                      profit.alias('net_profit_ttm_parent'),F.when(F.col('b.equity_parent')>0,profit/F.col('b.equity_parent')).alias('roe_ttm'),
                      F.when(revenue>0,(revenue-cost)/revenue).alias('gross_margin_ttm'),
                      F.to_date('p.effective_date').alias('_p'),F.to_date('a.effective_date').alias('_a'),F.to_date('s.effective_date').alias('_s'),F.to_date('b.effective_date').alias('_b'))
    # At each publication event, prefer the most recent visible component versions.
    window=Window.partitionBy('instrument_id','report_period','effective_date').orderBy(F.col('_p').desc(),F.col('_a').desc_nulls_last(),F.col('_s').desc_nulls_last(),F.col('_b').desc_nulls_last())
    return frame.withColumn('_choice',F.row_number().over(window)).where('_choice=1').drop('_choice','_p','_a','_s','_b').withColumn('ann_date',F.col('effective_date'))


def holder_longline(source):
    from pyspark.sql import functions as F,Window
    types=['social_security','mutual_fund','insurance','qfii','hk_connect']
    source=source.where(F.substring('report_period',6,5).isin('03-31','06-30','09-30','12-31')&F.col('ann_date').isNotNull())
    frame=source.groupBy('instrument_id','report_period').agg(F.sum(F.when(F.col('holder_type').isin(types),F.coalesce('pct_of_total',F.lit(0))).otherwise(0)).alias('inst_longline_ratio'),F.max(F.to_date('ann_date')).alias('effective_date'))
    chronology=Window.partitionBy('instrument_id').orderBy('effective_date','report_period')
    frame=frame.withColumn('_seen_report',F.max('report_period').over(chronology)).where('report_period=_seen_report').drop('_seen_report')
    previous=F.lag('report_period').over(chronology)
    quarter=F.year('report_period')*4+F.quarter('report_period')
    prev_quarter=F.year(previous)*4+F.quarter(previous)
    frame=frame.withColumn('inst_longline_chg',F.when(quarter-prev_quarter==1,F.col('inst_longline_ratio')-F.lag('inst_longline_ratio').over(chronology)))
    frame=frame.withColumn('_next',F.lead('effective_date').over(chronology))
    stale=(frame.where(F.col('_next').isNull()|(F.col('_next')>F.date_add('effective_date',200)))
           .select('instrument_id','report_period',F.date_add('effective_date',200).alias('effective_date'),F.lit(None).cast('double').alias('inst_longline_ratio'),F.lit(None).cast('double').alias('inst_longline_chg')))
    return frame.drop('_next').unionByName(stale).withColumn('ann_date',F.col('effective_date'))


def consensus(source):
    from pyspark.sql import functions as F,Window
    source=source.select('instrument_id',F.to_date('publish_date').alias('_publish'),F.col('forecast_year').cast('int').alias('_fy'),
                         F.coalesce('institution_id','institution_name').alias('_institution'),'forecast_net_profit',F.col('report_id').cast('double').alias('_report'))
    source=source.dropna(subset=['instrument_id','_publish','_fy','_institution','forecast_net_profit'])
    same_day=Window.partitionBy('instrument_id','_fy','_institution','_publish').orderBy(F.col('_report').desc_nulls_last(),F.col('forecast_net_profit').desc())
    source=source.withColumn('_choice',F.row_number().over(same_day)).where('_choice=1').drop('_choice')
    chronology=Window.partitionBy('instrument_id','_fy','_institution').orderBy('_publish')
    source=(source.withColumn('_next',F.lead('_publish').over(chronology))
            .withColumn('_expire',F.least(F.date_add('_publish',180),F.coalesce('_next',F.date_add('_publish',180))))
            .withColumn('_start',F.to_date(F.concat(F.col('_fy').cast('string'),F.lit('-05-01'))))
            .withColumn('_end',F.to_date(F.concat((F.col('_fy')+1).cast('string'),F.lit('-05-01')))))
    events=(source.select('instrument_id','_fy',F.explode(F.array('_publish','_expire','_start','_end')).alias('effective_date'))
            .where((F.col('effective_date')>=F.to_date(F.concat(F.col('_fy').cast('string'),F.lit('-05-01'))))&
                   (F.col('effective_date')<=F.to_date(F.concat((F.col('_fy')+1).cast('string'),F.lit('-05-01'))))).distinct())
    active=events.alias('e').join(source.alias('s'),(F.col('e.instrument_id')==F.col('s.instrument_id'))&(F.col('e._fy')==F.col('s._fy'))&
        (F.col('e.effective_date')>=F.col('s._publish'))&(F.col('e.effective_date')<F.col('s._expire'))&(F.col('e.effective_date')<F.col('s._end')),'left')
    result=active.groupBy(F.col('e.instrument_id').alias('instrument_id'),F.col('e._fy').alias('_fy'),F.col('e.effective_date').alias('effective_date')).agg(F.avg('s.forecast_net_profit').alias('consensus_fy1_mean'),F.percentile('s.forecast_net_profit',F.lit(.5)).alias('consensus_fy1_median'),F.count('s._institution').cast('double').alias('consensus_fy1_coverage'))
    # Rollover is explicit even when the new fiscal year has no forecast.
    result=result.withColumn('consensus_fy1_year',F.when(F.month('effective_date')<=4,F.year('effective_date')-1).otherwise(F.year('effective_date')).cast('double'))
    result=result.withColumn('_correct_year',(F.col('_fy')==F.col('consensus_fy1_year')).cast('int'))
    window=Window.partitionBy('instrument_id','effective_date').orderBy(F.col('_correct_year').desc(),F.col('_fy').desc())
    return result.withColumn('_choice',F.row_number().over(window)).where('_choice=1').drop('_choice','_fy','_correct_year').withColumn('ann_date',F.col('effective_date'))
