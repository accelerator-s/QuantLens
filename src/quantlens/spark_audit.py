from quantlens.warehouse import lineage,publish


def run(spark,context):
    from pyspark.sql import functions as F
    import pyarrow.parquet as pq
    from pathlib import Path
    from paths import workspace
    # Audit export is supplied by the host as business records in landing.
    path=Path('/data/landing/audit/records.parquet')
    if not path.exists():path=workspace().data/'landing/audit/records.parquet'
    records=pq.read_table(path).to_pylist()
    schema='id string, kind string, created_at string, body string, sha256 string'
    frame=spark.createDataFrame(records,schema).withColumn('as_of',F.current_date())
    publish(spark,lineage(frame,context),'ads.audit_log',['as_of'],run=context,allow_empty=True)
