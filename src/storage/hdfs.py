from __future__ import annotations

import json
import subprocess
import re
import os
from pathlib import Path, PurePosixPath

from exceptions import ContractViolation
from paths import workspace


def compose(*args: str, timeout: int = 300, check: bool = True, on_line=None):
    root = workspace().root
    if args and args[0] == 'up':
        from storage.cluster_access import prepare_access
        prepare_access()
    environment = {**os.environ, 'QUANTLENS_UID': str(os.getuid()), 'QUANTLENS_GID': str(os.getgid())}
    command = ['docker', 'compose', '--env-file', str(root/'docker/versions.env'),
               '-f', str(root/'docker/compose.yaml'), *map(str, args)]
    try:
        if on_line is None:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=environment)
        else:
            import threading
            from collections import deque
            output = deque(maxlen=10000)
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=environment) as process:
                def read_output():
                    for line in process.stdout:
                        output.append(line)
                        on_line(line.rstrip())
                reader = threading.Thread(target=read_output, daemon=True)
                reader.start()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
                finally:
                    reader.join(timeout=10)
                result = subprocess.CompletedProcess(command, process.returncode, ''.join(output), '')
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractViolation(code='CLUSTER_UNAVAILABLE', message='无法连接 Docker 集群',
                                suggested_action='运行 bash docker/cluster.sh up；本地开发显式设置 QUANTLENS_COMPUTE_LOCAL=1') from exc
    if check and result.returncode:
        output=result.stdout+'\n'+result.stderr
        causes=[line for line in output.splitlines() if line.startswith(('exceptions.','pyspark.errors.')) or re.match(r'^[\w.]+(?:Error|Exception):',line)]
        message=causes[-1][-2000:] if causes else (result.stderr[-2000:] or result.stdout[-2000:])
        raise ContractViolation(code='CLUSTER_COMMAND_FAILED', message=message,
                                suggested_action='运行 bash docker/cluster.sh status 并检查服务日志')
    return result


class HDFS:
    def command(self, *args: str, check: bool = True):
        return compose('exec', '-T', 'nn-rm', 'hdfs', 'dfs', *args, check=check)

    def mkdir(self, path: str):
        self.command('-mkdir', '-p', path)

    def exists(self, path: str) -> bool:
        result = self.command('-test', '-e', path, check=False)
        if result.returncode not in (0, 1):
            raise ContractViolation(code='HDFS_UNAVAILABLE', message=result.stderr[-1000:], suggested_action='检查 nn-rm 服务')
        return result.returncode == 0

    def put(self, source: str, destination: str):
        # source must be visible inside nn-rm, normally /data/landing/...
        self.command('-put', source, destination)

    def get(self, source: str, destination: str):
        self.command('-get', source, destination)
        path=PurePosixPath(destination)
        if path.is_absolute() and any(path.is_relative_to(PurePosixPath(root)) and path!=PurePosixPath(root) for root in ['/data/ads','/data/landing']):
            # The CLI runs as root inside nn-rm; hand downloaded business files
            # to the host user so manifests and temporary cleanup remain writable.
            compose('exec','-T','nn-rm','chown','-R',f'{os.getuid()}:{os.getgid()}',str(path))

    def ls(self, path: str) -> list[str]:
        return [line.split()[-1] for line in self.command('-ls', path).stdout.splitlines() if len(line.split()) >= 8]

    def mv(self, source: str, destination: str):
        self.command('-mv', source, destination)

    def rm(self, path: str):
        if not any(part in {'_tmp', '_blocked'} for part in PurePosixPath(path).parts):
            raise ValueError('只允许清理临时或阻断目录，研究数据不得删除')
        self.command('-rm', '-r', '-f', path)

    def read_json(self, path: str):
        return json.loads(self.command('-cat', path).stdout)

    def write_json(self, path: str, payload):
        import uuid
        landing = workspace().data/'landing'/'_meta'
        landing.mkdir(parents=True, exist_ok=True)
        local = landing/f'{uuid.uuid4().hex}.json'
        local.write_text(json.dumps(payload, ensure_ascii=False, default=str))
        try:
            self.mkdir(str(PurePosixPath(path).parent))
            self.put('/data/landing/_meta/'+local.name, path)
        finally:
            local.unlink(missing_ok=True)
