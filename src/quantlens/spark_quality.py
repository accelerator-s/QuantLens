from __future__ import annotations
from quantlens.warehouse import job, lineage, publish


def prices_gate(frame):
    from pyspark.sql import functions as F
    totals = frame.agg(F.count('*').alias('n'), F.sum((~F.col('ohlc_valid')).cast('int')).alias('invalid')).first()
    duplicate = frame.groupBy('code','date').count().where('count>1').count()
    result = []
    if duplicate: result.append({'rule':'duplicate_code_date','failed_rows':duplicate})
    if totals.n and (totals.invalid or 0)/totals.n >= .001:
        result.append({'rule':'invalid_ohlc','failed_rows':totals.invalid,'rows':totals.n})
    return result


def pit_gate(frame):
    bad = frame.where('effective_date < ann_date OR report_period > effective_date OR effective_date IS NULL')
    count = bad.count()
    return [{'rule':'pit_future_date','failed_rows':count,'samples':[r.asDict() for r in bad.limit(5).collect()]}] if count else []


def labels_gate(frame, universe):
    from pyspark.sql import functions as F
    # Tail dates without a complete global horizon are not mature labels.
    mature = frame.where('valid_until IS NOT NULL').join(universe.select('code','date','in_universe'), ['code','date']).where('in_universe')
    ratios = mature.groupBy('horizon').agg(F.avg(F.col('valid').cast('double')).alias('coverage')).collect()
    return [{'rule':'label_coverage','horizon':r.horizon,'coverage':r.coverage} for r in ratios if r.coverage <= .95]


def run(spark, context, *, include_labels=True):
    from pyspark.sql import functions as F
    rows = []
    for table, gate in [('dwd.prices_clean', prices_gate), ('dwd.fundamentals_pit', pit_gate), ('dwd.analyst_pit', pit_gate)]:
        failures = gate(spark.table(table))
        rows.append((table, not failures, str(failures), context['run_id'], context['data_snapshot'], context['version']))
    counts=spark.table('dwd.universe').where('in_universe').groupBy('date').count()
    unusual=counts.where('count<3000 OR count>6000')
    import json
    warning={'severity':'warning','rule':'universe_size','days':unusual.count(),
             'samples':[row.asDict() for row in unusual.orderBy(F.col('date').desc()).limit(10).collect()]}
    rows.append(('dwd.universe',True,json.dumps(warning,default=str),context['run_id'],context['data_snapshot'],context['version']))
    if include_labels and spark.catalog.tableExists('dwd.label_forward'):
        mature=(spark.table('dwd.label_forward').where('valid_until IS NOT NULL')
                .join(spark.table('dwd.universe').select('code','date','in_universe'),['code','date']).where('in_universe'))
        coverage=[row.asDict() for row in mature.groupBy('horizon').agg(F.avg(F.col('valid').cast('double')).alias('coverage'),F.count('*').alias('mature_rows')).orderBy('horizon').collect()]
        rows.append(('dwd.label_forward',all(item['coverage']>.95 for item in coverage),json.dumps({'horizons':coverage}),context['run_id'],context['data_snapshot'],context['version']))
    frame = spark.createDataFrame(rows, 'table string, passed boolean, failures string, run_id string, data_snapshot string, version string').withColumn('as_of', F.current_date())
    publish(spark, frame, 'dwd.quality_report', ['as_of'], run=context)

if __name__ == '__main__':
    with job('quality') as (spark, context): run(spark, context)
