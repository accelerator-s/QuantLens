from dataclasses import dataclass, field
from panel import cache, list_fields, FieldCategory
from panel.aggregates import AGGREGATE_STAGED_TABLES, aggregate_source_ready, build_aggregate_table
from quantlens import data, store
from paths import workspace

@dataclass
class SyncReport:
    new_rows: int = 0
    steps: list = field(default_factory=list)
    ok: bool = True

def rebuild(console, on_progress=None, *, only_stale=False):
    end=data.coverage()['end'][:10]
    rebuilt=[]
    for variant in ['default','strict']:
        if only_stale and cache.status(variant).state == 'fresh':
            console.print(f'{variant} 数据缓存可复用')
            continue
        console.print(f'构建 {variant} 数据缓存')
        fields=[f for f in list_fields() if variant=='default' or f.category==FieldCategory.FUNDAMENTAL]
        cache.rebuild(variant,fields,start='2000-01-01',end=end,
            on_field=lambda spec,stats: console.print(f'已完成 {spec.name}'))
        rebuilt.append(variant)
    return {'end':end, 'rebuilt':rebuilt}


def aggregate_needs_rebuild(name):
    staged=workspace().staged_for('CN-A','stock')
    target=staged/f'{name}.parquet'
    if not target.exists():return True
    return any((staged/f'{source}.parquet').stat().st_mtime_ns > target.stat().st_mtime_ns
               for source in AGGREGATE_STAGED_TABLES[name])

def run_sync(dp, *, console, yes=True, on_progress=None):
    def emit(**payload):
        if on_progress:
            on_progress(payload)
    def download_progress(payload):
        payload=dict(payload)
        payload['fraction']=float(payload.get('fraction', 0)) * .8
        payload['percent']=round(payload['fraction'] * 100, 1)
        emit(**payload)
    result=dp.update_all(console=console,on_progress=download_progress)
    rows=sum(o.stats.new_rows for o in result.outcomes if o.stats is not None)
    console.print('检查派生数据与计算缓存')
    emit(state='enriching', phase='更新派生数据', table='', fraction=.8, percent=80, pack_stage='', pack_percent=None)
    rebuilt=[]
    for name in AGGREGATE_STAGED_TABLES:
        if aggregate_source_ready(name) and aggregate_needs_rebuild(name):
            build_aggregate_table(name)
            rebuilt.append(name)
    emit(state='checking', phase='更新计算缓存', table='', fraction=.9, percent=90, pack_stage='', pack_percent=None)
    cached=rebuild(console,on_progress,only_stale=True)
    store.add('data_update',{'provider':dp.name,'rows':rows,'coverage':data.coverage(),
                            'rebuilt_aggregates':rebuilt,'rebuilt_caches':cached['rebuilt']})
    return SyncReport(rows,[('数据更新','done'),('派生数据','done' if rebuilt else 'reused'),
                           ('计算缓存','done' if cached['rebuilt'] else 'reused')])
