from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from paths import workspace
from storage.hdfs import HDFS


def staged_tables(root=None):
    root = Path(root) if root else workspace().staged_for('CN-A', 'stock')
    tables = {p.stem: [p] for p in sorted(root.glob('*.parquet'))}
    tables.update({p.name: sorted(p.glob('*.parquet')) for p in sorted(root.iterdir()) if p.is_dir() and list(p.glob('*.parquet'))})
    derived={'consensus_daily','holder_longline','fundamental_ttm'}
    return {name:files for name,files in tables.items() if not name.startswith('quality_') and name not in derived}


def hive_type(dtype):
    if pa.types.is_boolean(dtype): return 'boolean'
    if pa.types.is_integer(dtype): return 'bigint'
    if pa.types.is_floating(dtype): return 'double'
    if pa.types.is_date(dtype): return 'date'
    if pa.types.is_timestamp(dtype): return 'timestamp'
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype): return 'string'
    raise ValueError(f'Unsupported staged type: {dtype}')


def export(root=None, upload=True):
    result = []
    for table, files in staged_tables(root).items():
        schema = pq.read_schema(files[0])
        if any(not schema.equals(pq.read_schema(f), check_metadata=False) for f in files[1:]):
            raise ValueError(f'{table}: staged schemas differ')
        fields = [{'name': f.name, 'arrow_type': str(f.type), 'hive_type': hive_type(f.type), 'nullable': f.nullable} for f in schema]
        digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]
        doc = {'table': table, 'schema_version': digest, 'fields': fields}
        # JSON and DDL are schema artifacts, never copies of source/configuration.
        directory = workspace().root/'docker/ddl'
        directory.mkdir(parents=True, exist_ok=True)
        (directory/f'ods_{table}_{digest}.json').write_text(json.dumps(doc, indent=2))
        cols = ',\n'.join(f'  `{f["name"]}` {f["hive_type"]}' for f in fields)
        (directory/f'ods_{table}_{digest}.sql').write_text(f"CREATE EXTERNAL TABLE IF NOT EXISTS ods.{table} (\n{cols}\n)\nPARTITIONED BY (year int) STORED AS PARQUET\nLOCATION '/quantlens/ods/{table}'\nTBLPROPERTIES ('parquet.compression'='snappy');\n")
        if upload:
            hdfs = HDFS()
            uri = f'/quantlens/meta/schema/{table}/{digest}.json'
            if not hdfs.exists(uri): hdfs.write_json(uri, doc)
        result.append(doc)
    return result

if __name__ == '__main__':
    export()
