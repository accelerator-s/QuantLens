"""Start a fresh local web process on every launcher invocation."""
from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
import signal
import socket
import threading
import time
import webbrowser

import httpx
import uvicorn

from exceptions import ContractViolation


def _service_status(url: str, root: Path) -> dict | None:
    try:
        response = httpx.get(url + '/api/status', trust_env=False, timeout=2)
    except httpx.ConnectError:
        return None
    except httpx.TransportError as exc:
        raise ContractViolation(code='QL_PORT_BUSY', message='目标端口未返回有效服务响应',
                                suggested_action='检查占用端口的程序，或使用 --port 指定其他端口') from exc
    try:
        status = response.json() if response.status_code == 200 else None
        own_service = isinstance(status, dict) and status.get('workspace', {}).get('root') == str(root)
    except (ValueError, AttributeError):
        own_service = False
    if not own_service:
        raise ContractViolation(code='QL_PORT_BUSY', message='目标端口已被其他程序或项目占用',
                                suggested_action='关闭占用端口的程序，或使用 --port 指定其他端口')
    return status


def _stop_existing(status: dict) -> None:
    pid = status.get('server', {}).get('pid')
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        raise ContractViolation(code='QL_RESTART_OWNER_UNKNOWN', message='无法确认旧 QuantLens 服务的进程',
                                suggested_action='先结束旧服务，再运行 python main.py')
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise ContractViolation(code='QL_RESTART_DENIED', message='没有权限重启旧 QuantLens 服务',
                                suggested_action='使用启动旧服务的同一用户运行命令') from exc


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def _acquire(lock, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise ContractViolation(code='QL_START_TIMEOUT', message='等待前一次启动完成超时',
                                        suggested_action='检查 runs/logs/server.log 后重试')
            time.sleep(.2)


def _restart_and_lock(lock, url: str, root: Path, port: int, deadline: float) -> None:
    stopped_old = False
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if not stopped_old:
                status = _service_status(url, root)
                if status:
                    _stop_existing(status)
                    stopped_old = True
            if time.monotonic() >= deadline:
                raise ContractViolation(code='QL_RESTART_TIMEOUT', message='旧 QuantLens 服务未能及时退出',
                                        suggested_action='结束旧服务中的请求后重试，检查 runs/logs/server.log')
            time.sleep(.2)
    if not stopped_old:
        status = _service_status(url, root)
        if status:
            _stop_existing(status)
    while _port_open(port):
        if time.monotonic() >= deadline:
            raise ContractViolation(code='QL_RESTART_TIMEOUT', message='旧 QuantLens 服务尚未释放端口',
                                    suggested_action='结束旧服务中的请求后重试')
        time.sleep(.2)


def launch(root: Path, build_app, port=None, *, open_browser=True):
    try:
        port = int(port if port is not None else os.environ.get('QUANTLENS_PORT', '8610'))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ContractViolation(code='QL_INVALID_PORT', message='启动端口必须为 1 至 65535 的整数',
                                suggested_action='检查 --port 或 QUANTLENS_PORT 设置') from exc
    url = f'http://127.0.0.1:{port}'
    frontend = root / 'src/webui/frontend'
    for name in ('index.html', 'app.js', 'core/api.js', 'components/lens/lens.js'):
        if not (frontend / name).is_file():
            raise ContractViolation(code='QL_FRONTEND_MISSING', message=f'缺少前端文件：{name}',
                                    suggested_action='恢复完整的 src/webui/frontend 目录后重试')

    def show_frontend():
        if not open_browser:
            return
        try:
            opened = webbrowser.open(url)
        except webbrowser.Error as exc:
            opened = False
            logging.getLogger('quantlens').warning('打开浏览器失败：%s', exc)
        if not opened:
            import sys
            message = f'无法自动打开浏览器，请手动访问 {url}'
            logging.getLogger('quantlens').warning(message)
            print(message, file=sys.stderr, flush=True)

    # Serialize restart requests only until the new server is ready. The service
    # lock remains held throughout its lifetime to prevent duplicate listeners.
    with (root / f'.gui-{port}.launch.lock').open('a+') as transition:
        _acquire(transition, time.monotonic() + 60)
        with (root / f'.gui-{port}.lock').open('a+') as lock:
            _restart_and_lock(lock, url, root, port, time.monotonic() + 60)
            app = build_app()
            config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning',
                                    ws='none', timeout_graceful_shutdown=15)
            server = uvicorn.Server(config)
            stopped = threading.Event()
            failures = []

            def open_ready():
                deadline = time.monotonic() + 60
                while not stopped.wait(.2):
                    if server.started:
                        fcntl.flock(transition, fcntl.LOCK_UN)
                        show_frontend()
                        return
                    if server.should_exit:
                        return
                    if time.monotonic() >= deadline:
                        failures.append(ContractViolation(
                            code='QL_START_TIMEOUT', message='QuantLens 服务启动超时',
                            suggested_action='检查 runs/logs/server.log 后重试'))
                        server.should_exit = True
                        return

            opener = threading.Thread(target=open_ready, daemon=True)
            opener.start()
            try:
                server.run()
            except SystemExit as exc:
                if exc.code:
                    raise ContractViolation(code='QL_START_FAILED', message='QuantLens 服务启动失败',
                                            suggested_action='检查端口占用及 runs/logs/server.log') from exc
                raise
            finally:
                stopped.set()
                opener.join(timeout=3)
            if failures:
                raise failures[0]
            if not server.started:
                raise ContractViolation(code='QL_START_FAILED', message='QuantLens 服务未能启动',
                                        suggested_action='检查 runs/logs/server.log')
