from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from paths import workspace
from quantlens.schema_export import staged_tables, export
from quantlens.warehouse import make_run
from storage.hdfs import HDFS
from storage.hive import execute as execute_hive


def checksum(files):
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.name.encode())
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def ingest(root=None, *, cluster_checked=False):
    if not cluster_checked:
        from quantlens.compute import ensure_cluster
        ensure_cluster()
    run = make_run('ingest')
    hdfs = HDFS()
    manifest_path = workspace().data/'ingest_manifest.json'
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'partitions': {}}
    manifest = {'partitions': dict(previous['partitions']), **run}
    schemas = export(root)
    for schema in schemas:
        old=previous.get('schema_versions',{}).get(schema['table'])
        if old and old != schema['schema_version']:
            raise ValueError(f"{schema['table']}: schema changed ({old} -> {schema['schema_version']}); apply a reviewed DDL migration before publishing")
    manifest['schema_versions']={s['table']:s['schema_version'] for s in schemas}
    landing = workspace().data/'landing'/run['run_id']
    landing.mkdir(parents=True)
    try:
        for table, sources in staged_tables(root).items():
            table_root = landing/table
            table_root.mkdir()
            # Hard links expose original staged bytes without retaining another data copy.
            links = []
            for source in sources:
                target = table_root/source.name
                try: os.link(source, target)
                except OSError: shutil.copyfile(source, target)
                links.append(target)
            source_digest = checksum(links)
            if previous.get('sources', {}).get(table) == source_digest:
                continue
            date_column = next((c for c in ['date', 'effective_date', 'publish_date', 'ann_date', 'report_period'] if c in pq.read_schema(links[0]).names), None)
            # Scan each source once; split only landing data into year partitions.
            writers, rows = {}, {}
            try:
                for source in links:
                    for batch in pq.ParquetFile(source).iter_batches(batch_size=131072):
                        data = pa.Table.from_batches([batch])
                        years = pc.utf8_slice_codeunits(pc.cast(data[date_column], pa.string()), 0, 4) if date_column else pa.array(['0000']*len(data))
                        for year in sorted(set(years.to_pylist()), key=lambda x: x or ''):
                            year = year or '0000'
                            selected = data.filter(pc.fill_null(pc.equal(years, year), year == '0000'))
                            if not len(selected): continue
                            if year not in writers:
                                directory = table_root/f'year={year}'
                                directory.mkdir()
                                writers[year] = pq.ParquetWriter(directory/'part-00000.parquet', data.schema, compression='snappy')
                            writers[year].write_table(selected)
                            rows[year] = rows.get(year, 0)+len(selected)
            finally:
                for writer in writers.values(): writer.close()
            changed={}
            for year in sorted(rows):
                partition=table_root/f'year={year}'
                sha=checksum(list(partition.glob('*.parquet')))
                key=f'{table}/year={year}'
                if manifest['partitions'].get(key,{}).get('sha256')!=sha:
                    changed[year]=sha
            temporary=f'/quantlens/ods/{table}/_tmp/{run["run_id"]}'
            destination=f'/quantlens/ods/{table}/_runs/{run["run_id"]}'
            try:
                if changed:
                    hdfs.mkdir(temporary)
                    sources=[f'/data/landing/{run["run_id"]}/{table}/year={year}' for year in changed]
                    hdfs.command('-put',*sources,temporary)
                    hdfs.mkdir(str(Path(destination).parent))
                    hdfs.mv(temporary,destination)
                    schema=next(doc for doc in schemas if doc['table']==table)
                    ddl=(workspace().root/f'docker/ddl/ods_{table}_{schema["schema_version"]}.sql').read_text().rstrip(';\n')
                    statements=['CREATE DATABASE IF NOT EXISTS ods',ddl]
                    for year in changed:
                        uri=f'{destination}/year={year}'
                        statements.extend([f"ALTER TABLE ods.{table} ADD IF NOT EXISTS PARTITION (year={int(year)}) LOCATION '{uri}'",f"ALTER TABLE ods.{table} PARTITION (year={int(year)}) SET LOCATION '{uri}'"])
                    execute_hive(';'.join(statements)+';')
                    from datetime import datetime,UTC
                    for year,sha in changed.items():
                        manifest['partitions'][f'{table}/year={year}']={'run_id':run['run_id'],'table':table,'partition':f'year={year}','rows':rows[year],'sha256':sha,'source_uri':f'{destination}/year={year}','completed_at':datetime.now(UTC).isoformat()}
                print(f'{table}: {len(changed)} partitions, {sum(rows.values())} rows',flush=True)
            finally:
                hdfs.rm(temporary)
            manifest.setdefault('sources', {}).update({table: source_digest})
        manifest['sources'] = {**previous.get('sources', {}), **manifest.get('sources', {})}
        snapshot_payload=[{k:entry[k] for k in ['table','partition','rows','sha256']} for _,entry in sorted(manifest['partitions'].items())]
        manifest['data_snapshot']=hashlib.sha256(json.dumps(snapshot_payload,sort_keys=True).encode()).hexdigest()
        # Atomic manifest rename is publication of the ingestion batch to downstream jobs.
        pending = manifest_path.with_suffix('.pending')
        pending.write_text(json.dumps(manifest, indent=2))
        pending.replace(manifest_path)
        return manifest
    finally:
        shutil.rmtree(landing)


if __name__ == '__main__':
    ingest()
