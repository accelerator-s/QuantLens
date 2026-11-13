from dataclasses import dataclass, field
from quantlens import data, store
from paths import workspace

@dataclass
class SyncReport:
    new_rows: int = 0
    steps: list = field(default_factory=list)
    ok: bool = True

def rebuild(console, on_progress=None, *, only_stale=False):
    from quantlens.spark_daily import run
    return run(console.print)


def run_sync(dp, *, console, yes=True, on_progress=None):
    def emit(**payload):
        if on_progress:
            on_progress(payload)
    def download_progress(payload):
        payload=dict(payload)
        payload['fraction']=float(payload.get('fraction', 0)) * .8
        payload['percent']=round(payload['fraction'] * 100, 1)
        emit(**payload)
    from quantlens.compute import ensure_cluster
    emit(state='computing', phase='检查计算集群', table='', fraction=0, percent=0)
    ensure_cluster(console.print)
    result=dp.update_all(console=console,on_progress=download_progress)
    rows=sum(o.stats.new_rows for o in result.outcomes if o.stats is not None)
    emit(state='computing', phase='核对清洗缓存并发布数据', table='', fraction=.8, percent=80, pack_stage='', pack_percent=None)
    def pipeline_reporter(message):
        console.print(message)
    def pipeline_progress(payload):
        payload=dict(payload)
        percent=payload.get('percent')
        if percent is not None:
            payload['fraction']=.8 + min(100, max(0, float(percent))) * .0018
            payload['percent']=round(payload['fraction'] * 100, 1)
        payload['phase']=payload.get('phase') or payload.get('step') or '清洗并发布数据'
        emit(**payload)
    pipeline_reporter.progress=pipeline_progress
    from quantlens.spark_daily import run
    batch=run(pipeline_reporter, mode='data')
    store.add('data_update',{'provider':dp.name,'rows':rows,'batch_run_id':batch['run_id']})
    emit(state='completed', phase='数据同步完成', fraction=1, percent=100)
    return SyncReport(rows,[('数据接入','done'),('数据清洗与发布','done')])
