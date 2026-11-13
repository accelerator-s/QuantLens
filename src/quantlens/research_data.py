from __future__ import annotations
import hashlib,json,shutil,uuid
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq
from paths import workspace
from exceptions import ContractViolation


def extract(query):
    from quantlens.compute import submit
    from storage.hdfs import HDFS
    manifest_path=workspace().data/'ingest_manifest.json'
    if not manifest_path.exists():
        raise ContractViolation(code='WAREHOUSE_NOT_PUBLISHED',message='尚无已发布数据快照',suggested_action='先完成数据接入和分层批次')
    snapshot=json.loads(manifest_path.read_text())['data_snapshot']
    from quantlens.ads import manifest
    from quantlens.warehouse import make_run
    from factors.registry import FactorRegistry
    identity={'query':query,'snapshot':snapshot,'publication':manifest().get('run_id'),
              'pipeline_code_hash':make_run('research_identity')['pipeline_code_hash'],
              'factor_definitions':[meta.model_dump(mode='json') for meta in FactorRegistry().list()]}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    cache=workspace().data/'research_queries'/digest
    if not (cache/'manifest.json').exists():
        request_id='extract-'+uuid.uuid4().hex
        submit('spark_extract','--request',request_id,'--query',json.dumps(query))
        hdfs=HDFS();record=hdfs.read_json(f'/quantlens/meta/extracts/{request_id}/manifest/part-*')
        # nn-rm writes through its ADS mount; research evidence is then moved out
        # of the serving directory and never picked up by the ADS manifest.
        temporary=workspace().data/'ads/_tmp'/request_id
        temporary.parent.mkdir(parents=True,exist_ok=True)
        try:
            hdfs.get(record['source_uri'],f'/data/ads/_tmp/{request_id}')
            files=list(temporary.glob('*.parquet'))
            if sum(pq.ParquetFile(f).metadata.num_rows for f in files)!=record['rows']:raise ValueError('Research extract row mismatch')
            cache.parent.mkdir(parents=True,exist_ok=True)
            temporary.rename(cache)
            (cache/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,default=str))
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    frame=pd.concat([pd.read_parquet(file) for file in sorted(cache.glob("*.parquet"))],ignore_index=True)
    if 'date' in frame:frame.date=pd.to_datetime(frame.date)
    frame.attrs['lineage']=json.loads((cache/'manifest.json').read_text())
    return frame


def factor_matrix(name,version,start,end,*,standardized=True):
    frame=extract({'kind':'factor','factor':name,'version':version,'start':start,'end':end})
    return frame.pivot(index='date',columns='code',values='std_value' if standardized else 'value').sort_index()
