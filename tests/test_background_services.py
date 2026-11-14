"""The web app stays responsive while startup runs or fails in the background."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class BackgroundServices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, QUANTLENS_HOME=self.temp.name, QUANTLENS_RECOVERY='0')
        self.env.start()
        from webui.backend.tasks import TaskManager
        from webui.backend import services
        self.services = services
        self.manager = TaskManager()
        self.manager_patch = patch.object(services, 'manager', self.manager)
        self.manager_patch.start()
        self.release = threading.Event()
        self.entered = threading.Event()

    def tearDown(self):
        self.release.set()
        active = self.manager.latest(category='service_startup')
        if active:
            self.wait_done(active['task_id'])
        self.manager_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def wait_done(self, task_id):
        for _ in range(200):
            task = self.manager.get(task_id)
            if task['status'] != 'running':
                return task
            time.sleep(.01)
        self.fail('Background startup did not finish')

    def block_startup(self, reporter):
        reporter.progress({'phase': '启动计算集群', 'percent': 5})
        reporter('Hive 正在启动')
        self.entered.set()
        self.release.wait(2)

    def test_duplicate_launch_joins_existing_task(self):
        with patch.object(self.services, 'start_services', side_effect=self.block_startup) as start:
            first = self.services.start_background_services()
            self.assertTrue(self.entered.wait(1))
            second = self.services.start_background_services()
            self.assertEqual(first, second)
            start.assert_called_once()
            status = self.services.startup_status()
            self.assertEqual(status['status'], 'running')
            self.assertEqual(status['phase'], '启动计算集群')
            self.assertNotIn('log', status)
            self.assertTrue(self.manager.get(first)['log'][0].endswith(']  Hive 正在启动'))
            self.release.set()
            self.assertEqual(self.wait_done(first)['status'], 'completed')

    def test_html_and_status_respond_before_services_are_ready(self):
        from fastapi.testclient import TestClient
        from webui.backend.server import create_app
        with patch.object(self.services, 'start_services', side_effect=self.block_startup), \
             patch('webui.backend.server.AutomationScheduler.start'), \
             patch('webui.backend.server.AutomationScheduler.stop'):
            with TestClient(create_app()) as client:
                self.assertTrue(self.entered.wait(1))
                self.assertFalse(self.release.is_set())
                self.assertEqual(client.get('/').status_code, 200)
                status = client.get('/api/status').json()['service_startup']
                self.assertEqual(status['status'], 'running')
                self.assertEqual(status['phase'], '启动计算集群')
                self.release.set()
                self.assertEqual(self.wait_done(status['task_id'])['status'], 'completed')

    def test_cluster_summary_omits_logs_while_task_keeps_them(self):
        from quantlens.routes import cluster_status
        with patch.object(self.services, 'start_services', side_effect=self.block_startup), \
             patch('quantlens.compute.cluster_status', return_value={'available':False,'nodes':[]}), \
             patch('quantlens.gpu.worker_status', return_value={}), \
             patch('quantlens.ads.provenance', return_value={}):
            task_id=self.services.start_background_services()
            self.assertTrue(self.entered.wait(1))
            status=cluster_status()['service_startup']
            self.assertEqual(status['task_id'],task_id)
            self.assertEqual(status['phase'],'启动计算集群')
            self.assertNotIn('log',status)
            self.assertTrue(self.manager.get(task_id)['log'][0].endswith(']  Hive 正在启动'))
            self.release.set()
            self.assertEqual(self.wait_done(task_id)['status'],'completed')

    def test_failure_remains_visible_and_can_be_retried(self):
        from exceptions import ContractViolation
        with patch.object(self.services, 'start_services', side_effect=ContractViolation(
                code='CLUSTER_NOT_READY', message='Hive 启动失败', suggested_action='检查 Hive 日志')):
            task_id = self.services.start_background_services()
            self.assertEqual(self.wait_done(task_id)['status'], 'failed')
        status = self.services.startup_status()
        self.assertIn('Hive 启动失败', status['error'])
        self.assertTrue(any(line.endswith(']  建议：检查 Hive 日志') for line in self.manager.get(task_id)['log']))
        with patch.object(self.services, 'start_services'):
            retry = self.services.start_background_services()
            self.assertNotEqual(retry, task_id)
            self.assertEqual(self.wait_done(retry)['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
