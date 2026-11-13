"""Startup must wait for the cluster and training worker before announcing readiness."""
from pathlib import Path
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from exceptions import ContractViolation
from quantlens.bootstrap import start_services


class StartupServices(unittest.TestCase):
    def test_services_start_in_dependency_order(self):
        calls = []
        reporter = Mock()
        with patch('quantlens.compute.ensure_cluster', side_effect=lambda log: calls.append('cluster')), \
             patch('quantlens.gpu.ensure_worker', side_effect=lambda: calls.append('worker')):
            start_services(reporter)
        self.assertEqual(calls, ['cluster', 'worker'])
        self.assertEqual(reporter.call_args.args[0], '集群与训练服务已就绪')

    def test_cluster_failure_prevents_worker_and_readiness_message(self):
        failure = ContractViolation(code='CLUSTER_NOT_READY', message='hive is unhealthy')
        reporter = Mock()
        with patch('quantlens.compute.ensure_cluster', side_effect=failure), \
             patch('quantlens.gpu.ensure_worker') as worker:
            with self.assertRaises(ContractViolation) as raised:
                start_services(reporter)
        self.assertIs(raised.exception, failure)
        worker.assert_not_called()
        self.assertNotIn('集群与训练服务已就绪', [call.args[0] for call in reporter.call_args_list])


    def test_worker_failure_prevents_readiness_message(self):
        reporter = Mock()
        with patch('quantlens.compute.ensure_cluster'), \
             patch('quantlens.gpu.ensure_worker', side_effect=ContractViolation(
                 code='TRAINING_WORKER_UNAVAILABLE', message='worker failed')):
            with self.assertRaises(ContractViolation):
                start_services(reporter)
        self.assertNotIn('集群与训练服务已就绪', [call.args[0] for call in reporter.call_args_list])


class WorkerReadiness(unittest.TestCase):
    def test_recent_heartbeat_does_not_hide_dead_worker(self):
        from quantlens.gpu import worker_status
        with tempfile.TemporaryDirectory() as temp, \
             patch('quantlens.gpu.shared_root', return_value=Path(temp)), \
             patch.dict(os.environ, SPARK_HOME=''):
            path = Path(temp) / 'worker.json'
            path.write_text(json.dumps({'pid': 1234, 'heartbeat': time.time()}))
            with patch('quantlens.gpu.os.kill', side_effect=ProcessLookupError):
                self.assertEqual(worker_status(), {})
            with patch('quantlens.gpu.os.kill') as probe:
                self.assertEqual(worker_status()['pid'], 1234)
                probe.assert_called_once_with(1234, 0)



if __name__ == '__main__':
    unittest.main()
