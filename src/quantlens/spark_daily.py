"""Host orchestration. All batch compute is submitted to spark-client on YARN."""
from quantlens.warehouse import make_run


def run(reporter=print, *, ingest_data=True, mode='research', start_at=None, factor_start=None):
    if mode not in {'data', 'research'}:
        raise ValueError('Unknown batch mode: '+mode)
    from quantlens.ingest_hdfs import ingest
    from quantlens.compute import ensure_cluster, submit
    from quantlens.sync_ads import sync
    ensure_cluster(reporter)
    batch_id=make_run('batch')['run_id']
    if ingest_data:
        reporter('接入 staged 数据到 ODS')
        ingest(cluster_checked=True)
    from quantlens.audit_export import export
    export()
    reporter('清洗行情、财务、预期和行业数据' if mode=='data' else '提交 DWD → 因子 → 权重 → 组合 → ADS 批次')
    arguments=['--batch',batch_id,'--mode',mode]
    if start_at:arguments.extend(['--from',start_at])
    if factor_start:arguments.extend(['--factor-start',str(factor_start)])
    submit('spark_batch',*arguments,reporter=reporter)
    from quantlens.integrations import ensure_hive_hbase
    reporter('绑定 Hive/HBase 已发布快照表')
    ensure_hive_hbase()
    reporter('同步已发布 ADS')
    result=sync(batch_id)
    from quantlens.model_comparison import apply_conclusions
    if mode=='research' and apply_conclusions():
        metadata_batch=make_run('metadata')['run_id']
        submit('sync_factor_meta','--batch',metadata_batch)
        result=sync(metadata_batch)
    return result

if __name__=='__main__': run()
