from __future__ import annotations
import yaml
from paths import workspace
from exceptions import ContractViolation


def fields(spark, names, start=None, end=None):
    from pyspark.sql import functions as F, Window
    mappings = yaml.safe_load((workspace().config/'panel_fields.yaml').read_text())['fields']
    prices = spark.table('dwd.prices_clean')
    from quantlens.security_master import A_SHARE_PATTERN
    prices = prices.where(F.col('code').rlike(A_SHARE_PATTERN))
    if start:prices=prices.where(F.col('date')>=F.lit(start))
    if end:prices=prices.where(F.col('date')<=F.lit(end))
    unknown=[name for name in names if name not in mappings]
    if unknown:
        raise ContractViolation(code='PANEL_FIELD_MISSING',message=f'缺少字段映射: {unknown}',suggested_action='补齐 config/panel_fields.yaml')
    direct=[F.col(mappings[name]['column']).alias(name) for name in sorted(set(names)) if mappings[name]['table']=='dwd.prices_clean']
    base=prices.select('code','date',*direct)
    for name in sorted(set(names)):
        mapping=mappings[name]
        if mapping['table']=='dwd.prices_clean':continue
        events = spark.table(mapping['table'])
        if 'item' in mapping:
            events = events.where(F.col('item')==mapping['item'])
            value = 'value'
        else:
            if 'classification_id' in mapping:
                events = events.where(F.col('classification_id')==mapping['classification_id'])
            value = mapping['column']
        # max(report_period, effective_date) retains the latest visible report,
        # including later corrections, without letting an old report replace it.
        report = F.coalesce(F.col('report_period'), F.col('effective_date')) if 'report_period' in events.columns else F.col('effective_date')
        events = events.select('code', F.col('effective_date').alias('date'),
                               F.struct(report.alias('report'), F.col('effective_date').alias('effective'), F.col(value).alias('value')).alias('_event'))
        events = events.groupBy('code','date').agg(F.max('_event').alias('_event'))
        if end:events=events.where(F.col('date')<=F.lit(end))
        if start:
            prior=(events.where(F.col('date')<F.lit(start)).groupBy('code').agg(F.max('_event').alias('_event'))
                   .withColumn('date',F.date_sub(F.to_date(F.lit(start)),1)))
            events=events.where(F.col('date')>=F.lit(start)).unionByName(prior)
        daily = prices.select('code','date').join(events, ['code','date'], 'full')
        window = Window.partitionBy('code').orderBy('date').rowsBetween(Window.unboundedPreceding, Window.currentRow)
        daily = daily.withColumn('_visible', F.max('_event').over(window)).select('code','date', F.col('_visible.value').alias(name))
        base = base.join(daily, ['code','date'], 'left')
    return base
