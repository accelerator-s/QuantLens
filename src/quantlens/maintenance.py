import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from exceptions import ContractViolation
from paths import workspace
from quantlens import store

def backup(reporter, full=False):
    raise ContractViolation(
        code='QL_BACKUP_DISABLED',
        message='已按工作区要求禁用备份',
        suggested_action='直接使用当前工作区，不创建备份或恢复副本',
    )


def archive_hash(archive,name):
    digest=hashlib.sha256()
    with archive.open(name) as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def verify(backup_id):
    record=store.get(backup_id)
    if record['kind']!='backup':
        raise ContractViolation(code='QL_BACKUP_INVALID',message='请选择备份记录',suggested_action='从备份列表选择')
    archive_path=workspace().root/record['body']['path']
    if not archive_path.is_file():
        raise ContractViolation(code='QL_BACKUP_REMOVED',message='备份文件已清理',suggested_action='备份已禁用，请使用当前工作区')
    with zipfile.ZipFile(archive_path) as archive:
        manifest=json.loads(archive.read('backup.json'))
        for entry in manifest['entries']:
            p=Path(entry['path'])
            if p.is_absolute() or '..' in p.parts:
                raise ContractViolation(code='QL_BACKUP_PATH',message='备份路径越界',suggested_action='使用本机生成的备份')
            if archive_hash(archive,entry['path'])!=entry['sha256']:
                raise ContractViolation(code='QL_BACKUP_HASH',message='备份文件校验失败',suggested_action='选择另一份完整备份')
    return {'backup_id':backup_id,'files':len(manifest['entries']),'verified':True,'scope':manifest['scope'],'full':manifest.get('full',False)}

def restore(backup_id, actor):
    raise ContractViolation(
        code='QL_BACKUP_DISABLED',
        message='已按工作区要求禁用恢复副本',
        suggested_action='直接使用当前工作区',
    )


def inspect_recovery(recovery_id):
    record=store.get(recovery_id)
    if record['kind']!='recovery':
        raise ContractViolation(code='QL_RECOVERY_REQUIRED',message='请选择恢复记录',suggested_action='从恢复列表选择')
    root=workspace().root/record['body']['path']
    if not root.is_dir():
        raise ContractViolation(code='QL_RECOVERY_REMOVED',message='恢复副本已清理',suggested_action='恢复副本已清理，请使用当前工作区')
    manifest=json.loads((root/'backup.json').read_text())
    verified=all(file_hash(root/e['path'])==e['sha256'] for e in manifest['entries'])
    with sqlite3.connect(f'file:{root / "experience/quantlens.sqlite"}?mode=ro',uri=True) as db:
        rows=db.execute('SELECT kind,count(*) FROM records GROUP BY kind').fetchall()
    return {'records':[{'kind':k,'count':n} for k,n in rows],
            'models':len(list((root/'runs/spark').glob('*/model/metadata/part-*'))),
            'configs':len(list((root/'config').glob('*'))),'verified':verified,'full':manifest.get('full',False),
            'launched':(root.parent/f'{record["id"]}-server.json').exists()}


def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def launch_recovery(recovery_id):
    import os
    import socket
    import subprocess
    import sys
    import time
    import httpx
    record=store.get(recovery_id)
    if record['kind']!='recovery':
        raise ContractViolation(code='QL_RECOVERY_REQUIRED',message='请选择恢复记录',suggested_action='从恢复列表选择')
    root=workspace().root/record['body']['path']
    state_path=root.parent/f'{record["id"]}-server.json'
    if state_path.exists():
        state=json.loads(state_path.read_text())
        try:
            status=httpx.get(state['url']+'/api/status',trust_env=False,timeout=2)
            if status.status_code==200 and status.json()['workspace']['root']==str(root):return state
        except httpx.TransportError:
            state={'state':'stopped'}
    report=inspect_recovery(recovery_id)
    if not report['full'] or (not report['verified'] and not report['launched']):
        raise ContractViolation(code='QL_RECOVERY_INCOMPLETE',message='独立打开需要校验通过的完整备份',suggested_action='恢复副本已禁用，请使用当前工作区')
    port=None
    for candidate in range(8611,8631):
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',candidate))
            except OSError:continue
        port=candidate
        break
    if port is None:
        raise ContractViolation(code='QL_RECOVERY_PORT_BUSY',message='恢复服务端口已占用',suggested_action='关闭不用的恢复副本')
    url=f'http://127.0.0.1:{port}'
    env={**os.environ,'QUANTLENS_HOME':str(root),'QUANTLENS_RECOVERY':'1','QUANTLENS_COMPUTE_LOCAL':'1'}
    with (root.parent/f'{record["id"]}-server.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1',
            '--port',str(port),'--ws','none','--log-level','warning'],cwd=root,env=env,stdout=log,stderr=log,start_new_session=True)
    state={'pid':process.pid,'url':url,'recovery_id':recovery_id,'root':str(root)}
    for _ in range(40):
        if process.poll() is not None:break
        try:
            status=httpx.get(url+'/api/status',trust_env=False,timeout=1)
            if status.status_code==200 and status.json()['workspace']['root']==str(root):
                store.write_json(state_path,state)
                store.add('recovery_launch',state)
                return state
        except httpx.TransportError:
            time.sleep(.25)
    process.terminate()
    raise ContractViolation(code='QL_RECOVERY_START_FAILED',message='恢复界面未能启动',suggested_action='在任务日志中检查启动失败原因')


def stop_recovery(recovery_id):
    import os
    import signal
    record=store.get(recovery_id)
    if record['kind']!='recovery':
        raise ContractViolation(code='QL_RECOVERY_REQUIRED',message='请选择恢复记录',suggested_action='从恢复列表选择')
    root=workspace().root/record['body']['path']
    state_path=root.parent/f'{record["id"]}-server.json'
    if state_path.exists():
        state=json.loads(state_path.read_text())
        process=Path(f'/proc/{state["pid"]}')
        if process.exists() and (process/'cwd').resolve()==root:os.kill(state['pid'],signal.SIGTERM)
    return {'state':'stopped'}
