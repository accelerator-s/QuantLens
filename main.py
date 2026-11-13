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
    from webui.launcher import launch as launch_webui

    return launch_webui(ROOT, build_app, port, open_browser=open_browser)


def cli():
    import argparse

    parser = argparse.ArgumentParser(description='启动 QuantLens 集群、训练服务与网页', add_help=False)
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
