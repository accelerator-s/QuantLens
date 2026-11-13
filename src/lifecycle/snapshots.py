from __future__ import annotations
import hashlib,json
from datetime import UTC,date,datetime,time
from exceptions import LifecycleError
from paths import workspace
from .models import DataSnapshot,ObjectType,QualityStatus
from .store import LifecycleStore


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def _cutoff(value):
    return datetime.combine(date.fromisoformat(value),time.max,tzinfo=UTC)


class DataSnapshotService:
    def __init__(self,store=None):self.store=store or LifecycleStore()

    def create(self,*,require_strict=False):
        path=workspace().data/'ingest_manifest.json'
        if not path.exists():
            raise LifecycleError(code='DATA_SNAPSHOT_MANIFEST_MISSING',message='尚无已发布的接入清单',suggested_action='先完成 ODS 接入和 DWD 质检')
        manifest=json.loads(path.read_text());entries=manifest['partitions']
        from quantlens.ads import manifest as serving_manifest,query
        serving=serving_manifest();quality=query('quality_report',limit=100)
        if serving.get('data_snapshot')!=manifest['data_snapshot'] or not serving.get('data_as_of') or not quality or any(not r['passed'] for r in quality):
            raise LifecycleError(code='DATA_SNAPSHOT_QUALITY_BLOCKED',message='DWD 质检未完成或存在阻断',suggested_action='查看质检报告并发布完整批次')
        snapshot_id='DS-'+manifest['data_snapshot'][:20].upper()
        try:return self.store.get(ObjectType.DATA_SNAPSHOT,snapshot_id)
        except LifecycleError as exc:
            if exc.code!='LIFECYCLE_REFERENCE_NOT_FOUND':raise
        fingerprints={key:entry['sha256'] for key,entry in entries.items()}
        sources={key:entry['source_uri'] for key,entry in entries.items()}
        snapshot=DataSnapshot(snapshot_id=snapshot_id,as_of=_cutoff(serving['data_as_of']),market='CN-A',instrument_class='stock',
            manifest_refs=['data/ingest_manifest.json',*sources.values()],dataset_fingerprints=fingerprints,
            pit_cutoffs={'dwd':_cutoff(serving['data_as_of'])},universe_ref='hdfs:///quantlens/dwd/universe',
            market_rule_ref='CN-A-stock',quality_status=QualityStatus.PASSED,
            source_uris=sources,partitions=sorted(entries),row_counts={key:entry['rows'] for key,entry in entries.items()},
            parent_snapshot_id=self._latest_snapshot_id('CN-A','stock'))
        self.store.add(snapshot);return snapshot

    def _latest_snapshot_id(self,market,instrument_class):
        for row in self.store.list_objects(ObjectType.DATA_SNAPSHOT,limit=5000):
            item=self.store.get(ObjectType.DATA_SNAPSHOT,row['object_id'])
            if item.market==market and item.instrument_class==instrument_class:return item.snapshot_id
        return None
