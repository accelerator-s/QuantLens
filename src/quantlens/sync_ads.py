from __future__ import annotations
import hashlib,json,shutil
from pathlib import Path
import pyarrow.parquet as pq
from paths import workspace
from storage.hdfs import HDFS
from quantlens.warehouse import batch_identifier

def _publish_manifest(path, manifest):
    if manifest.get('scope') == 'backtest' and path.exists():
        previous = json.loads(path.read_text())
        manifest = {**previous, 'tables': {**previous.get('tables', {}), **manifest['tables']}}
    if manifest.get('scope')=='data' and path.exists():
        previous=json.loads(path.read_text())
        manifest={**manifest,'tables':{**previous.get('tables',{}),**manifest['tables']}}
    pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
    pending.replace(path)
    return manifest

def sync(batch_id):
    batch_identifier(batch_id)
    hdfs=HDFS()
    doc=hdfs.read_json(f'/quantlens/meta/batches/{batch_id}/manifest/part-*')
    root=workspace().data/'ads';root.mkdir(parents=True,exist_ok=True)
    temporary=root/'_tmp'/batch_id
    final=root/'batches'/batch_id
    if final.exists():
        stored=final/'manifest.json'
        if stored.exists():existing=json.loads(stored.read_text())
        else:
            current=workspace().data/'ads_manifest.json'
            existing=json.loads(current.read_text()) if current.exists() else {}
        if existing.get('run_id')!=doc['run_id'] or set(existing.get('tables',{}))!=set(doc['tables']):
            raise ValueError('Existing ADS batch manifest does not match')
        for name,entry in existing['tables'].items():
            if entry['source_uri']!=doc['tables'][name]['source_uri'] or entry['rows']!=doc['tables'][name]['rows']:raise ValueError('Existing ADS table does not match')
            for item in entry['files']:
                file=(root/item['path']).resolve()
                if final.resolve() not in file.parents or not file.is_file():raise ValueError('Existing ADS file is missing')
                with file.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                if digest!=item['sha256']:raise ValueError('Existing ADS checksum mismatch')
        return _publish_manifest(workspace().data/'ads_manifest.json',existing)
    temporary.mkdir(parents=True,exist_ok=False)
    try:
        for name,entry in doc['tables'].items():
            hdfs.get(entry['source_uri'],f'/data/ads/_tmp/{batch_id}/{name}')
            files=sorted((temporary/name).glob('*.parquet'))
            if sum(pq.ParquetFile(file).metadata.num_rows for file in files)!=entry['rows']:
                raise ValueError(f'ADS row count mismatch: {name}')
            entry['files']=[]
            for file in files:
                with file.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                entry['files'].append({'path':f'batches/{batch_id}/{name}/{file.name}','sha256':digest,'rows':pq.ParquetFile(file).metadata.num_rows})
        (temporary/'manifest.json').write_text(json.dumps(doc,indent=2,ensure_ascii=False))
        final.parent.mkdir(exist_ok=True)
        temporary.rename(final)
        return _publish_manifest(workspace().data/'ads_manifest.json',doc)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('batch_id');sync(parser.parse_args().batch_id)
