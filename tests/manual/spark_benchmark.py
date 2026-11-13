"""Run the same warehouse aggregation used by the Hive/MR course experiment."""
import json,time
from quantlens.warehouse import job

QUERY='SELECT year, count(*) AS n, sum(volume) AS volume FROM dwd.prices_clean WHERE year=2025 GROUP BY year'
with job('course_benchmark') as (spark,context):
    started=time.perf_counter()
    rows=[row.asDict() for row in spark.sql(QUERY).collect()]
    print('QUANTLENS_BENCH='+json.dumps({'query':QUERY,'rows':rows,'query_seconds':time.perf_counter()-started,'spark_version':spark.version,**context}),flush=True)
