"""Publish validated current-format research results to canonical ADS tables."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from quantlens.backtest_publication import completed_result
from quantlens.warehouse import _fs, job, lineage, publish, root_uri

SCHEMAS = {
    'nav': 'date string, nav double, cash double, position_value double, daily_return double, benchmark_return double, excess_return double, turnover_oneside double, buy_amount double, sell_amount double, cost_paid double, n_holdings long',
    'trades': 'date string, instrument string, side string, status string, target_weight double, shares long, anchor_price double, exec_price double, amount double, commission double, transfer_fee double, stamp_tax double, other_fees double, slippage_cost double, note string',
    'positions': 'date string, instrument string, market_value double, shares_nominal long, weight double',
}


def run(spark, context, run_ids, batch):
    from pyspark.sql import functions as F
    from quantlens.spark_ads import export_backtests
    results = [completed_result(run_id) for run_id in run_ids]
    if len(set(run_ids)) != len(run_ids) or not results:
        raise ValueError('Expected unique completed runs')
    tables = {}
    for name, schema in SCHEMAS.items():
        chunks = []
        for result in results:
            pdf = result['frames'][name].copy()
            saved_profile = result['profile']
            profile = saved_profile.extra.get('source_strategy_id') or saved_profile.profile_name
            pdf['backtest_run'] = result['run_id']
            pdf['strategy_id'] = profile
            pdf['as_of'] = result['config'].end
            pdf['run_id'] = context['run_id']
            pdf['data_snapshot'] = result['snapshot']
            pdf['version'] = context['version']
            chunks.append(pdf)
        combined = pd.concat(chunks, ignore_index=True)
        schema += ', backtest_run string, strategy_id string, as_of string, run_id string, data_snapshot string, version string'
        types = {'string': pa.string(), 'double': pa.float64(), 'long': pa.int64()}
        arrow_schema = pa.schema([pa.field(column, types[kind])
                                  for column, kind in (part.split() for part in schema.split(','))])
        with tempfile.TemporaryDirectory(prefix='backtest-publication-') as directory:
            local = Path(directory) / (name + '.parquet')
            pq.write_table(pa.Table.from_pandas(combined, schema=arrow_schema, preserve_index=False), local)
            uri = f'{root_uri()}/meta/batches/{batch}/inputs/{name}.parquet'
            fs, destination = _fs(spark, uri)
            fs.mkdirs(destination.getParent())
            fs.copyFromLocalFile(False, False, spark._jvm.org.apache.hadoop.fs.Path(local.as_uri()), destination)
        tables['backtest_' + name] = (spark.read.schema(schema).parquet(uri)
            .withColumn('date', F.to_date('date')).withColumn('as_of', F.to_date('as_of')))
    rows = [(r['run_id'], r['profile'].extra.get('source_strategy_id') or r['profile'].profile_name,
             json.dumps(r['metrics'], default=str), r['saved']['protocol_hash'], r['snapshot'],
             json.dumps(r['saved'], default=str), json.dumps(r['status'], default=str), r['config'].end)
            for r in results]
    summary = spark.createDataFrame(rows, 'backtest_run string, strategy_id string, metrics string, protocol_hash string, input_snapshot string, config string, status string, as_of string')
    summary = lineage(summary.withColumn('as_of', F.to_date('as_of')), context)
    tables['backtest_summary'] = summary
    # Validate existing IDs before any write. The same immutable run cannot be replaced.
    if spark.catalog.tableExists('ads.backtest_summary'):
        existing = spark.table('ads.backtest_summary').where(F.col('backtest_run').isin(run_ids)).collect()
        expected = {r['run_id']: r for r in results}
        for row in existing:
            result = expected[row.backtest_run]
            if (json.loads(row.config) != json.loads(json.dumps(result['saved'], default=str))
                    or json.loads(row.metrics) != json.loads(json.dumps(result['metrics'], default=str))):
                raise ValueError(f'Published run changed: {row.backtest_run}')
    for name, frame in tables.items():
        if spark.catalog.tableExists('ads.' + name):
            old = spark.table('ads.' + name).where(~F.col('backtest_run').isin(run_ids))
            dates = [r['config'].end for r in results]
            old = old.where(F.date_format('as_of', 'yyyy-MM-dd').isin(dates))
            frame = old.unionByName(frame)
        publish(spark, frame, 'ads.' + name, ['as_of'], run=context, allow_empty=True)
        print(f'QUANTLENS_BACKTEST=已发布 {name}', flush=True)
    export_backtests(spark, context, batch, max(r['config'].end for r in results))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-ids', required=True)
    parser.add_argument('--batch', required=True)
    args = parser.parse_args()
    with job('publish_research_backtests') as (spark, context):
        run(spark, context, json.loads(args.run_ids), args.batch)
