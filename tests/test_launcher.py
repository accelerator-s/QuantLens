"""Repeated launch replaces the web process and serves a fresh frontend."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from exceptions import ContractViolation
from webui import launcher

FIXTURE = '''
import os,sys
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from webui.launcher import launch
import webbrowser
root=Path(sys.argv[1]);port=int(sys.argv[2]);instance=uuid4().hex

def open_page(url):
    with (root/'browser.log').open('a') as stream:stream.write(url+'\\n')
    return True
webbrowser.open=open_page

def app_factory():
    app=FastAPI()
    html=(root/'src/webui/frontend/index.html').read_text()
    @app.on_event('shutdown')
    def shutdown():
        with (root/'shutdown.log').open('a') as stream:stream.write(str(os.getpid())+'\\n')
    @app.get('/api/status')
    def status():return {'workspace':{'root':str(root)},'server':{'pid':os.getpid(),'instance_id':instance}}
    @app.get('/')
    def home():return HTMLResponse(html)
    return app
launch(root,app_factory,port,open_browser=sys.argv[3]=='browser')
'''


class Launcher(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        frontend = self.root / 'src/webui/frontend'
        for relative in ('index.html', 'app.js', 'core/api.js', 'components/lens/lens.js'):
            path = frontend / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('first frontend')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.port = reservation.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.processes = []
        self.addCleanup(self.stop_processes)

    def stop_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def start(self, browser=False):
        environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
        process = subprocess.Popen([sys.executable, '-c', FIXTURE, str(self.root), str(self.port), 'browser' if browser else 'no-browser'],
                                   env=environment, cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.processes.append(process)
        return process

    def ready(self, process):
        for _ in range(150):
            if process.poll() is not None:
                self.fail(process.stderr.read().decode())
            try:
                with urlopen(self.url + '/api/status', timeout=.3) as response:
                    result = json.load(response)
                    if result['server']['pid'] == process.pid:
                        return result
            except (URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(.05)
        self.fail('new server did not become ready')

    def browser_count(self, count):
        path = self.root / 'browser.log'
        for _ in range(50):
            if path.exists() and len(path.read_text().splitlines()) >= count:
                return path.read_text().splitlines()
            time.sleep(.05)
        self.fail('frontend was not opened')

    def test_second_launch_replaces_process_and_frontend(self):
        first = self.start(browser=True)
        initial = self.ready(first)
        self.browser_count(1)
        (self.root / 'src/webui/frontend/index.html').write_text('updated frontend')
        second = self.start(browser=True)
        restarted = self.ready(second)
        self.assertIn(first.wait(timeout=5), (0, -signal.SIGTERM))
        self.assertIn(str(first.pid), (self.root / 'shutdown.log').read_text().splitlines())
        self.assertNotEqual(initial['server']['instance_id'], restarted['server']['instance_id'])
        self.assertEqual(urlopen(self.url).read().decode(), 'updated frontend')
        self.assertEqual(self.browser_count(2), [self.url, self.url])

    def test_concurrent_invocations_leave_one_live_server(self):
        first, second = self.start(), self.start()
        for _ in range(160):
            exited = [process for process in (first, second) if process.poll() is not None]
            if exited:
                self.assertIn(exited[0].returncode, (0, -signal.SIGTERM), exited[0].stderr.read().decode())
                self.assertIn(str(exited[0].pid), (self.root / 'shutdown.log').read_text().splitlines())
                live = second if exited[0] is first else first
                self.ready(live)
                break
            time.sleep(.05)
        else:
            self.fail('concurrent invocations did not complete their restart')
        self.assertFalse((self.root / 'browser.log').exists())

    def test_foreign_project_is_never_stopped(self):
        import httpx
        response = httpx.Response(200, json={'workspace': {'root': '/other/project'}, 'server': {'pid': 12345}})
        with patch.object(launcher.httpx, 'get', return_value=response), patch.object(launcher.os, 'kill') as kill:
            with self.assertRaises(ContractViolation) as raised:
                launcher.launch(self.root, lambda: self.fail('must not build app'), self.port, open_browser=False)
        self.assertEqual(raised.exception.code, 'QL_PORT_BUSY')
        kill.assert_not_called()

    def test_missing_process_identity_has_no_fallback(self):
        with patch.object(launcher.os, 'kill') as kill:
            with self.assertRaises(ContractViolation) as raised:
                launcher._stop_existing({'workspace': {'root': str(self.root)}})
        self.assertEqual(raised.exception.code, 'QL_RESTART_OWNER_UNKNOWN')
        kill.assert_not_called()

    def test_invalid_port_is_rejected_before_building(self):
        for port in (0, 65536, 'invalid'):
            with self.subTest(port=port), self.assertRaises(ContractViolation) as raised:
                launcher.launch(self.root, lambda: self.fail('must not build app'), port)
            self.assertEqual(raised.exception.code, 'QL_INVALID_PORT')

    def test_restart_waits_for_port_release(self):
        status = {'server': {'pid': 12345}}
        path = self.root / 'server.lock'
        with path.open('a+') as lock, patch.object(launcher, '_service_status', return_value=status), \
             patch.object(launcher, '_stop_existing') as stop, patch.object(launcher, '_port_open', return_value=True):
            with self.assertRaises(ContractViolation) as raised:
                launcher._restart_and_lock(lock, self.url, self.root, self.port, deadline=time.monotonic() - 1)
        stop.assert_called_once_with(status)
        self.assertEqual(raised.exception.code, 'QL_RESTART_TIMEOUT')


if __name__ == '__main__':
    unittest.main()
