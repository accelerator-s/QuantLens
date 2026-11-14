"""Docker/YARN is the sole cluster runtime."""
import json
import os
import re
from storage.hdfs import compose
from exceptions import ContractViolation

CLUSTER_SERVICES = frozenset({'nn-rm', 'dn-nm-1', 'dn-nm-2', 'hive', 'hbase',
                              'metastore-db', 'spark-client', 'spark-history', 'cluster-access'})


def cluster_status():
    response = compose('ps','--all','--format','json',check=False,timeout=20)
    if response.returncode:
        return {'nodes':[], 'available':False,'error':response.stderr[-1000:]}
    try:
        parsed = json.loads(response.stdout) if response.stdout.strip().startswith('[') else [json.loads(line) for line in response.stdout.splitlines() if line.strip()]
    except ValueError:
        parsed = []
    healthy={node['Service'] for node in parsed if node['State']=='running' and node.get('Health')=='healthy'}
    return {'nodes':[{'name':n['Service'],'running':n['State']=='running','health':n.get('Health',''),'state':n['State']} for n in parsed], 'available':CLUSTER_SERVICES<=healthy,
            'unavailable_services': sorted(CLUSTER_SERVICES - healthy), 'master':'yarn'}


def start_cluster(reporter):
    reporter('启动 HDFS/YARN/Hive/HBase/Spark 容器')
    compose('up','-d','--wait','--wait-timeout','540',timeout=600,on_line=reporter)
    status = cluster_status()
    if not status['available']:
        missing = ', '.join(status.get('unavailable_services', sorted(CLUSTER_SERVICES)))
        raise ContractViolation(code='CLUSTER_NOT_READY', message=f'集群服务尚未就绪：{missing}',
                                suggested_action='在“设置 → 集群”检查服务状态和日志后重试；已下载的 staged 数据会保留。')
    return status


def ensure_cluster(reporter=print):
    """Recover a partial/stopped cluster before downloading or publishing data."""
    status = cluster_status()
    if status['available']:
        return status
    missing = ', '.join(status.get('unavailable_services', sorted(CLUSTER_SERVICES)))
    reporter(f'计算集群未就绪（{missing}），正在启动并等待健康检查通过…')
    try:
        status = start_cluster(reporter)
    except ContractViolation as exc:
        raise ContractViolation(code='CLUSTER_NOT_READY',
                                message=f'计算集群启动失败，尚未开始数据入库：{exc.message}',
                                suggested_action='在“设置 → 集群”检查服务状态和日志后重试；已下载的 staged 数据会保留，无需删除或重新下载。') from exc
    reporter('计算集群已就绪')
    return status


def stop_cluster():
    compose('stop',timeout=180)
    return cluster_status()


def submit(module, *arguments, reporter=None):
    name = module.removeprefix('quantlens.')
    if not name.replace('_','').isalnum(): raise ValueError('Invalid job name')
    if os.environ.get('QUANTLENS_COMPUTE_LOCAL') != '1':
        ensure_cluster(reporter or print)
    from quantlens.gpu import ensure_worker
    ensure_worker()
    if os.environ.get('QUANTLENS_COMPUTE_LOCAL')=='1':
        import subprocess,sys
        result = subprocess.run([sys.executable,'-m','quantlens.'+name,*map(str,arguments)],check=True)
        return result.returncode
    last_scheduler_state = None

    def stage_log(line):
        nonlocal last_scheduler_state
        scheduler = re.search(r'Application report for (application_\d+_\d+) \(state: (\w+)\)', line)
        if reporter and scheduler:
            application, state = scheduler.groups()
            if (application, state) != last_scheduler_state:
                last_scheduler_state = (application, state)
                if state in {'SUBMITTED', 'ACCEPTED'}:
                    reporter(f'集群任务 {application} 正在排队等待计算资源。')
                    if hasattr(reporter, 'progress'):
                        reporter.progress({'state': 'queued', 'step': '等待集群计算资源'})
                elif state == 'RUNNING':
                    reporter(f'集群任务 {application} 已获得资源，开始运行。')
                    if hasattr(reporter, 'progress'):
                        reporter.progress({'state': 'running', 'step': '集群计算中'})
            return
        if reporter and line.startswith('QUANTLENS_PROGRESS='):
            if hasattr(reporter, 'progress'):
                reporter.progress(json.loads(line.partition('=')[2]))
        elif reporter and line.startswith('QUANTLENS_'):
            reporter(line)
    try:
        compose('exec','-T','spark-client','spark-submit','--master','yarn','--deploy-mode','client',
                '/workspace/src/quantlens/'+name+'.py',*map(str,arguments),timeout=14400, **({'on_line': stage_log} if reporter else {}))
    except ContractViolation as exc:
        from storage.hdfs import HDFS
        from quantlens.warehouse import make_run
        evidence=make_run('submission')
        try:
            HDFS().write_json(f'/quantlens/meta/runs/submissions/{name}/{evidence["run_id"]}/error.json',
                {'code':exc.code,'stage':name,'partition':'','message':exc.message,'hint':exc.suggested_action,**evidence})
        except (ContractViolation,OSError):pass
        raise
