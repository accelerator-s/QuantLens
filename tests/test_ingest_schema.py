import json
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from paths import workspace
from quantlens import ingest_hdfs, schema_export


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    root = tmp_path / 'staged'
    root.mkdir()
    table = pa.table({'code': ['000001'], 'date': ['2026-11-20'], 'close': [10.]})
    pq.write_table(table, root / 'prices_daily.parquet')
    doc = schema_export.export(root, upload=False)[0]
    workspace().data.mkdir(exist_ok=True)
    manifest = {'partitions': {}, 'schema_versions': {'prices_daily': doc['schema_version']}}
    (workspace().data / 'ingest_manifest.json').write_text(json.dumps(manifest))
    return root, table, doc


def test_column_reordering_keeps_published_schema(staged):
    root, table, doc = staged
    pq.write_table(table.select(['date', 'close', 'code']), root / 'prices_daily.parquet')
    assert schema_export.export(root, upload=False)[0] == doc


def test_partition_column_order_is_normalized(staged):
    root, table, doc = staged
    (root / 'prices_daily.parquet').unlink()
    parts = root / 'prices_daily'
    parts.mkdir()
    pq.write_table(table.select(['date', 'close', 'code']), parts / 'one.parquet')
    pq.write_table(table.select(['close', 'code', 'date']), parts / 'two.parquet')
    assert schema_export.export(root, upload=False)[0] == doc
    pq.write_table(table.set_column(2, 'close', pa.array([10])), parts / 'two.parquet')
    with pytest.raises(ValueError, match='schemas differ'):
        schema_export.export(root, upload=False)


def test_ingestion_writes_values_in_published_order(staged, monkeypatch):
    root, table, doc = staged
    pq.write_table(table.select(['date', 'close', 'code']), root / 'prices_daily.parquet')
    hdfs = Mock()
    uploads = []
    def upload(command, *args):
        assert command == '-put'
        for source in args[:-1]:
            path = workspace().data / Path(source).relative_to('/data') / 'part-00000.parquet'
            uploads.append(pq.ParquetFile(path).read())
    hdfs.command.side_effect = upload
    monkeypatch.setattr(ingest_hdfs, 'HDFS', lambda: hdfs)
    monkeypatch.setattr(schema_export, 'HDFS', lambda: hdfs)
    monkeypatch.setattr(ingest_hdfs, 'execute_hive', Mock())
    result = ingest_hdfs.ingest(root, cluster_checked=True)
    assert len(uploads) == 1
    assert uploads[0].equals(table)
    assert result['schema_versions']['prices_daily'] == doc['schema_version']


@pytest.mark.parametrize('change', ['type', 'added', 'removed', 'nullable'])
def test_real_schema_changes_still_block_before_hdfs_write(staged, monkeypatch, change):
    from exceptions import ContractViolation
    root, table, _ = staged
    if change == 'type':
        table = table.set_column(2, 'close', pa.array([10]))
    elif change == 'added':
        table = table.append_column('new_field', pa.array([1]))
    elif change == 'removed':
        table = table.drop(['close'])
    else:
        table = table.cast(pa.schema([pa.field(f.name, f.type, nullable=False) for f in table.schema]))
    pq.write_table(table, root / 'prices_daily.parquet')
    hdfs = Mock()
    monkeypatch.setattr(ingest_hdfs, 'HDFS', lambda: hdfs)
    monkeypatch.setattr(schema_export, 'HDFS', lambda: hdfs)
    with pytest.raises(ContractViolation) as error:
        ingest_hdfs.ingest(root, cluster_checked=True)
    assert error.value.code == 'INGEST_SCHEMA_CHANGED'
    hdfs.command.assert_not_called()
    assert not list((workspace().data / 'landing').glob('*'))
