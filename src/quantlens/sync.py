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
    emit(state='computing', phase='清洗并发布数据', table='', fraction=.9, percent=90, pack_stage='', pack_percent=None)
    from quantlens.spark_daily import run
    batch=run(console.print, mode='data')
    store.add('data_update',{'provider':dp.name,'rows':rows,'batch_run_id':batch['run_id']})
    return SyncReport(rows,[('数据接入','done'),('数据清洗与发布','done')])
