"""Shared Spark session, immutable partition publication and lineage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from contextlib import contextmanager
from datetime import UTC, datetime

from paths import workspace
from exceptions import ContractViolation

IDENTIFIER = re.compile(r'^[a-z][a-z0-9_]*$')


def identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f'Invalid identifier: {value}')
    return value


def batch_identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',value):
        raise ValueError('Invalid batch id')
    return value


def local_mode():
    return os.environ.get('QUANTLENS_COMPUTE_LOCAL') == '1'


def root_uri():
    return str(workspace().data/'warehouse') if local_mode() else 'hdfs://nn-rm:9000/quantlens'


def session(name):
    from pyspark.sql import SparkSession
    builder = SparkSession.builder.appName('QuantLens:'+name)
    if local_mode():
        builder = (builder.master('local[2]').config('spark.sql.shuffle.partitions', '4')
                   .config('spark.executorEnv.PYTHONPATH', str(Path(__file__).resolve().parents[1]))
                   .config('spark.sql.warehouse.dir', str(workspace().data/'warehouse'/'_catalog'))
                   .config('spark.eventLog.enabled', 'false').config('spark.driver.host', '127.0.0.1'))
    elif not os.environ.get('SPARK_HOME') or not os.path.isdir('/workspace'):
        raise ContractViolation(code='SPARK_CONTAINER_REQUIRED', message='集群作业必须通过 spark-client 提交',
                                suggested_action='运行 python -m quantlens.spark_daily；本地开发显式设置 QUANTLENS_COMPUTE_LOCAL=1')
    else:
        builder = builder.enableHiveSupport()
    return builder.config('spark.sql.session.timeZone', 'Asia/Shanghai').getOrCreate()


def make_run(job, snapshot=''):
    manifest = workspace().data/'ingest_manifest.json'
    if not snapshot and manifest.exists():
        snapshot = json.loads(manifest.read_text()).get('data_snapshot') or hashlib.sha256(manifest.read_bytes()).hexdigest()
    digest=hashlib.sha256()
    source_root=Path(__file__).resolve().parents[1]
    for path in sorted(source_root.rglob('*.py')):
        digest.update(str(path.relative_to(source_root)).encode());digest.update(path.read_bytes())
    return {'run_id': f'{job}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}',
            'data_snapshot': snapshot or 'unpublished', 'version': 'v1','pipeline_code_hash':digest.hexdigest()}


def lineage(frame, run, version=None):
    from pyspark.sql import functions as F
    for key in ('run_id', 'data_snapshot', 'version'):
        if key == 'version' and key in frame.columns and version is None:
            continue
        frame = frame.withColumn(key, F.lit(version if key == 'version' and version else run[key]))
    return frame


def _fs(spark, uri):
    path = spark._jvm.org.apache.hadoop.fs.Path(uri)
    return path.getFileSystem(spark._jsc.hadoopConfiguration()), path


def write_json(spark, uri, payload):
    frame = spark.createDataFrame([(json.dumps(payload, ensure_ascii=False, default=str),)], ['value'])
    frame.coalesce(1).write.mode('errorifexists').text(uri)


@contextmanager
def job(name, snapshot=''):
    spark = session(name)
    run = make_run(name, snapshot)
    try:
        yield spark, run
    except Exception as exc:
        write_json(spark, f'{root_uri()}/meta/runs/{name}/{run["run_id"]}/error.json', {
            'code': getattr(exc, 'code', 'JOB_FAILED'), 'stage': name, 'partition': getattr(exc, 'partition', ''),
            'message': str(exc), 'hint': '检查 Spark History 和质检报告后重试', **run})
        raise
    finally:
        spark.stop()


def publish(spark, frame, table, partitions=(), *, run, quality=None, allow_empty=False):
    """Write immutable data first, then atomically switch each Hive partition URI.

    Failed writes/quality checks never change published table pointers. Superseded
    partition locations remain research evidence, with the manifest recording both.
    """
    from pyspark.sql import functions as F
    layer, name = table.split('.')
    identifier(layer); identifier(name)
    for key in partitions:
        identifier(key)
    base = f'{root_uri()}/{layer}/{name}'
    write_id = run['run_id']+'-'+uuid.uuid4().hex[:8]
    temporary = f'{base}/_tmp/{write_id}'
    published = f'{base}/_runs/{write_id}'
    frame = frame.persist()
    try:
        if quality:
            violations = quality(frame)
            if violations:
                write_json(spark, f'{base}/_blocked/{run["run_id"]}/quality', {'failures': violations, **run})
                raise ContractViolation(code='QUALITY_BLOCKED', message=f'{table} 未通过质检: {violations}', suggested_action='修复质检报告列出的数据后重跑')
        rows = frame.count()
        if rows == 0 and not allow_empty:
            raise ContractViolation(code='EMPTY_PARTITION', message=f'{table} 没有可发布记录', suggested_action='检查上游分区与日期范围')
        writer = frame.coalesce(max(1, min(16, (rows*max(1,len(frame.columns))*8)//(64*1024*1024)+1))).write.mode('errorifexists').option('compression', 'snappy')
        if partitions:
            writer = writer.partitionBy(*partitions)
        writer.parquet(temporary)
        fs, src = _fs(spark, temporary)
        _, dst = _fs(spark, published)
        fs.mkdirs(dst.getParent())
        if not fs.rename(src, dst):
            raise RuntimeError('Cannot publish immutable run directory')
        spark.sql(f'CREATE DATABASE IF NOT EXISTS {layer}')
        data_fields = [field for field in frame.schema if field.name not in partitions]
        columns = ','.join(f'`{f.name}` {f.dataType.simpleString()}' for f in data_fields)
        partition_ddl = ','.join(f'`{p}` {frame.schema[p].dataType.simpleString()}' for p in partitions)
        location = base+'/empty' if partitions else published
        if local_mode():
            all_columns = ','.join(f'`{f.name}` {f.dataType.simpleString()}' for f in frame.schema)
            partition_names = ','.join(partitions)
            spark.sql(f"CREATE TABLE IF NOT EXISTS {table} ({all_columns}) USING PARQUET "
                      + (f'PARTITIONED BY ({partition_names}) ' if partitions else '')
                      + f"LOCATION '{location}'")
        else:
            spark.sql(f"CREATE EXTERNAL TABLE IF NOT EXISTS {table} ({columns}) "
                      + (f'PARTITIONED BY ({partition_ddl}) ' if partitions else '')
                      + f"STORED AS PARQUET LOCATION '{location}' TBLPROPERTIES ('parquet.compression'='snappy')")

        actual = {f.name: f.dataType.simpleString() for f in spark.table(table).schema}
        expected = {f.name: f.dataType.simpleString() for f in frame.schema}
        if actual != expected:
            raise ContractViolation(code='SCHEMA_MIGRATION_REQUIRED', message=f'{table} schema 变化，拒绝静默发布', suggested_action='创建新 DDL 版本并记录迁移说明')
        locations = []
        if partitions:
            for row in frame.select(*partitions).distinct().orderBy(*partitions).collect():
                parts = row.asDict()
                def literal(v):
                    return str(v).replace("'", "''")
                spec = ','.join(f"{p}='{literal(parts[p])}'" for p in partitions)
                suffix = '/'.join(f'{p}={parts[p]}' for p in partitions)
                uri = f'{published}/{suffix}'
                spark.sql(f"ALTER TABLE {table} ADD IF NOT EXISTS PARTITION ({spec}) LOCATION '{uri}'")
                spark.sql(f"ALTER TABLE {table} PARTITION ({spec}) SET LOCATION '{uri}'")
                locations.append({'partition': parts, 'source_uri': uri})
        else:
            spark.sql(f"ALTER TABLE {table} SET LOCATION '{published}'")
        spark.catalog.refreshTable(table)
        write_json(spark, f'{root_uri()}/meta/runs/publish/{run["run_id"]}/{layer}_{name}_{write_id}',
                   {'table': table, 'rows': rows, 'partitions': locations, **run})
        return {'table': table, 'rows': rows, 'partitions': locations, **run}
    finally:
        frame.unpersist()
        fs, path = _fs(spark, temporary)
        if fs.exists(path):
            fs.delete(path, True)


def read(spark, table):
    return spark.table(table)
