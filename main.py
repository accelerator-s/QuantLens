"""启动 QuantLens：同一服务提供前端页面和后端接口。"""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QUANTLENS_HOME'] = str(ROOT)


def build_app():
    import logging
    from logging.handlers import RotatingFileHandler
    from quantlens.bootstrap import prepare
    from webui.backend.server import create_app

    logs = ROOT / 'runs/logs'
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / 'server.log', maxBytes=4_000_000,
                                  backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.getLogger('quantlens').addHandler(handler)
    prepare()
    return create_app()


def launch(port=None, *, open_browser=True):
    import fcntl
    import logging
    import threading
    import time
    import webbrowser
    import httpx
    import uvicorn
    from exceptions import ContractViolation

    try:
        port = int(port if port is not None else os.environ.get('QUANTLENS_PORT', '8610'))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ContractViolation(code='QL_INVALID_PORT', message='启动端口必须为 1 至 65535 的整数',
                                suggested_action='检查 --port 或 QUANTLENS_PORT 设置') from exc
    url = f'http://127.0.0.1:{port}'

    def is_ready():
        try:
            response = httpx.get(url + '/api/status', trust_env=False, timeout=2)
        except httpx.ConnectError:
            return False
        except httpx.TransportError as exc:
            raise ContractViolation(code='QL_PORT_BUSY', message=f'端口 {port} 未返回有效服务响应',
                                    suggested_action='检查占用端口的程序，或使用 --port 指定其他端口') from exc
        try:
            status = response.json() if response.status_code == 200 else None
            own_service = isinstance(status, dict) and status.get('workspace', {}).get('root') == str(ROOT)
        except (ValueError, AttributeError):
            own_service = False
        if not own_service:
            raise ContractViolation(code='QL_PORT_BUSY', message=f'端口 {port} 已被其他程序占用',
                                    suggested_action='关闭占用端口的程序，或使用 --port 指定其他端口')
        return True

    def show_frontend():
        print(f'QuantLens 前后端已就绪：{url}', flush=True)
        if open_browser:
            try:
                opened = webbrowser.open(url)
            except webbrowser.Error as exc:
                opened = False
                logging.getLogger('quantlens').warning('打开浏览器失败：%s', exc)
            if not opened:
                message = f'无法自动打开浏览器，请手动访问 {url}'
                logging.getLogger('quantlens').warning(message)
                print(message, file=sys.stderr, flush=True)

    if is_ready():
        show_frontend()
        return

    # 每个端口分别加锁；重复启动时等待首个进程完成初始化。
    with (ROOT / f'.gui-{port}.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if is_ready():
                    show_frontend()
                    return
                time.sleep(.2)
            raise ContractViolation(code='QL_START_TIMEOUT', message='等待 QuantLens 启动超时',
                                    suggested_action='检查 runs/logs/server.log，确认前一次启动是否失败')
        # 另一个启动进程可能已在探测与加锁之间完成启动。
        if is_ready():
            show_frontend()
            return
        frontend = ROOT / 'src/webui/frontend'
        for name in ('index.html', 'app.js', 'core/api.js', 'components/lens/lens.js'):
            if not (frontend / name).is_file():
                raise ContractViolation(code='QL_FRONTEND_MISSING', message=f'缺少前端文件：{name}',
                                        suggested_action='恢复完整的 src/webui/frontend 目录后重试')
        app = build_app()
        config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', ws='none')
        server = uvicorn.Server(config)
        stopped = threading.Event()
        failures = []

        def open_ready():
            deadline = time.monotonic() + 60
            while not stopped.wait(.2):
                if server.started:
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
                                    suggested_action='检查端口占用及 runs/logs/server.log')


def cli():
    import argparse

    parser = argparse.ArgumentParser(description='一起启动 QuantLens 前端页面与后端接口', add_help=False)
    parser._optionals.title = '启动选项'
    parser.add_argument('-h', '--help', action='help', help='显示启动说明并退出')
    parser.add_argument('--port', metavar='端口', help='本机端口，默认读取 QUANTLENS_PORT 或使用 8610')
    parser.add_argument('--no-browser', action='store_true', help='启动服务但不自动打开浏览器')
    args = parser.parse_args()
    try:
        launch(args.port, open_browser=not args.no_browser)
    except Exception as exc:
        from webui.backend.errors import unexpected_error
        from exceptions import QuantTrainingError

        error = exc if isinstance(exc, QuantTrainingError) else unexpected_error(exc)
        message = f'[{error.code}] {error.message}\n{error.suggested_action or "请检查运行环境"}'
        print(message, file=sys.stderr, flush=True)
        if not args.no_browser and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            try:
                import tkinter as tk
                from tkinter import messagebox
                window = tk.Tk()
                window.withdraw()
                messagebox.showerror('QuantLens', message)
                window.destroy()
            except (ImportError, RuntimeError) as dialog_error:
                print(f'无法显示错误窗口：{dialog_error}', file=sys.stderr)
            except tk.TclError as dialog_error:
                print(f'无法显示错误窗口：{dialog_error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
else:
    app = build_app()
