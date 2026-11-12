import json
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
import pyspark

from exceptions import ContractViolation
from paths import workspace
from quantlens import store

def cluster_status():
    path=workspace().root/'.spark'/'cluster.json'
    state=json.loads(path.read_text()) if path.exists() else {'nodes':[]}
    for item in state['nodes']:
        p=Path(f"/proc/{item['pid']}/cmdline")
        item['running']=p.exists() and item['class'] in p.read_text().replace('\x00',' ')
    return state

def start_cluster(reporter):
    if any(n['running'] for n in cluster_status()['nodes']):
        raise ContractViolation(code='QL_CLUSTER_EXISTS',message='计算集群已启动',suggested_action='查看现有节点状态')
    root=workspace().root/'.spark'
    root.mkdir(exist_ok=True)
    binary=Path(pyspark.__file__).parent/'bin'/'spark-class'
    env={**os.environ,'SPARK_LOCAL_IP':'127.0.0.1','SPARK_WORKER_DIR':str(root/'workers'),
         'SPARK_HISTORY_OPTS':f'-Dspark.history.fs.logDirectory={root / "events"} -Dspark.history.ui.port=8093'}
    (root/'events').mkdir(exist_ok=True)
    nodes=[]
    specs=[('master','org.apache.spark.deploy.master.Master',['--host','127.0.0.1','--port','7079','--webui-port','8090'])]
    specs += [(f'worker{i}','org.apache.spark.deploy.worker.Worker',
               ['--host','127.0.0.1','--port',str(7080+i),'--webui-port',str(8090+i),'-c','2','-m','4g','spark://127.0.0.1:7079']) for i in [1,2]]
    specs += [('history','org.apache.spark.deploy.history.HistoryServer',[])]
    for name,cls,args in specs:
        with (root/f'{name}.log').open('ab') as log:
            proc=subprocess.Popen([str(binary),cls,*args],stdout=log,stderr=log,env=env,start_new_session=True)
        nodes.append({'name':name,'pid':proc.pid,'class':cls})
        store.write_json(root/'cluster.json',{'nodes':nodes,'master':'spark://127.0.0.1:7079'})
        reporter(f'启动 {name}')
    for attempt in range(30):
        time.sleep(1)
        try:
            response=httpx.get('http://127.0.0.1:8090/json/',trust_env=False,timeout=2)
            response.raise_for_status()
            ready=response.json()
            if len([n for n in ready.get('workers',[]) if n.get('state')=='ALIVE'])>=2:
                store.write_json(workspace().config/'compute.json',{'master':'spark://127.0.0.1:7079'})
                return cluster_status()
        except httpx.TransportError:
            if attempt==29:
                stop_cluster()
                raise
    stop_cluster()
    raise ContractViolation(code='QL_CLUSTER_NOT_READY',message='计算节点未能就绪',suggested_action='在任务中心查看节点日志')

def stop_cluster():
    for node in reversed(cluster_status()['nodes']):
        if node['running']:os.kill(node['pid'],signal.SIGTERM)
    store.write_json(workspace().config/'compute.json',{'master':'local'})
    return {'state':'stopped'}

def master(cores):
    if os.environ.get('QUANTLENS_COMPUTE_LOCAL')=='1':return f'local[{cores}]'
    path=workspace().config/'compute.json'
    configured=json.loads(path.read_text())['master'] if path.exists() else 'local'
    if configured!='local' and (not cluster_status()['nodes'] or not all(n['running'] for n in cluster_status()['nodes'])):
        raise ContractViolation(code='QL_CLUSTER_STOPPED',message='计算集群未运行',suggested_action='到任务中心启动集群，或点击停止集群切回本地计算')
    return f'local[{cores}]' if configured=='local' else configured
