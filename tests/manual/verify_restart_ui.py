"""Verify process replacement and automatic frontend reload in an isolated workspace.

Requires Chrome CDP on QUANTLENS_CDP_PORT (default 9237). No user data is changed.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

import websocket

ROOT = Path(__file__).resolve().parents[2]
CDP = os.environ.get('QUANTLENS_CDP_PORT', '9237')
SCRIPT = '''
import sys
from pathlib import Path
from quantlens.bootstrap import prepare
from webui.backend.server import create_app
from webui.launcher import launch
prepare()
launch(Path(sys.argv[1]),create_app,int(sys.argv[2]),open_browser=False)
'''
with tempfile.TemporaryDirectory(prefix='quantlens-restart-ui-') as temporary:
    directory = Path(temporary)
    frontend = directory / 'src/webui/frontend'
    frontend.parent.mkdir(parents=True)
    frontend.symlink_to(ROOT / 'src/webui/frontend', target_is_directory=True)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    processes = []
    environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'QUANTLENS_HOME': temporary, 'QUANTLENS_RECOVERY': '1'}
    page = json.load(urlopen(Request(f'http://127.0.0.1:{CDP}/json/new?about:blank', method='PUT')))
    ws = websocket.create_connection(page['webSocketDebuggerUrl'], suppress_origin=True)
    serial = 0

    def call(method, params=None):
        global serial
        serial += 1
        ws.send(json.dumps({'id': serial, 'method': method, 'params': params or {}}))
        while True:
            response = json.loads(ws.recv())
            if response.get('id') == serial:
                assert 'error' not in response, response
                return response.get('result', {})

    def js(expression):
        result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True, 'replMode': True})
        assert 'exceptionDetails' not in result, result
        return result.get('result', {}).get('value')

    def start():
        log = (directory / 'server.log').open('ab')
        process = subprocess.Popen([sys.executable, '-c', SCRIPT, temporary, str(port)],
                                   env=environment, stdout=log, stderr=log)
        log.close()
        processes.append(process)
        for _ in range(150):
            assert process.poll() is None, (directory / 'server.log').read_text()[-2000:]
            try:
                status = json.load(urlopen(base + '/api/status', timeout=.5))
                if status['server']['pid'] == process.pid:
                    return status
            except Exception:
                pass
            time.sleep(.1)
        raise AssertionError('server failed to become ready')

    try:
        initial = start()
        call('Page.enable')
        navigation = call('Page.navigate', {'url': base + '/#factors'})
        assert 'errorText' not in navigation, navigation
        for _ in range(100):
            try:
                if js("document.querySelector('[data-health]') && (await import('/core/health.js')).getHealth()?.server?.instance_id"):
                    break
            except AssertionError:
                pass
            time.sleep(.1)
        else:
            raise AssertionError('frontend did not receive initial health status')
        origin = js('performance.timeOrigin')
        restarted = start()
        assert restarted['server']['instance_id'] != initial['server']['instance_id']
        processes[0].wait(timeout=5)
        for _ in range(150):
            try:
                if js('performance.timeOrigin') != origin and js("(await import('/core/health.js')).getHealth()?.server?.instance_id") == restarted['server']['instance_id']:
                    break
            except AssertionError:
                pass
            time.sleep(.1)
        else:
            raise AssertionError('frontend did not reload after backend restart')
        assert js('location.hash') == '#factors'
        print('PASS: real backend PID/instance replaced; existing browser automatically reloaded and kept its route.')
    finally:
        ws.close()
        urlopen(f'http://127.0.0.1:{CDP}/json/close/{page["id"]}').close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
