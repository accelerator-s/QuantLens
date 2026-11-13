"""A partial Docker cluster must recover before downloading or publishing data."""
import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from exceptions import ContractViolation
from quantlens import compute


class ClusterReadiness(unittest.TestCase):
    def status(self, **overrides):
        return [dict(Service=name, State='running', Health='healthy')
                | overrides.get(name, {}) for name in sorted(compute.CLUSTER_SERVICES)]

    def test_status_reports_stopped_starting_and_absent_services(self):
        nodes = self.status(hive={'State': 'exited', 'Health': ''},
                            hbase={'Health': 'starting'})
        nodes = [node for node in nodes if node['Service'] != 'spark-client']
        for stdout in [json.dumps(nodes), '\n'.join(map(json.dumps, nodes))]:
            with self.subTest(format=stdout[0]), patch.object(compute, 'compose',
                    return_value=SimpleNamespace(returncode=0, stdout=stdout)):
                status = compute.cluster_status()
            self.assertFalse(status['available'])
            self.assertEqual(status['unavailable_services'], ['hbase', 'hive', 'spark-client'])

    def test_healthy_cluster_is_not_restarted(self):
        with patch.object(compute, 'cluster_status', return_value={'available': True}), \
             patch.object(compute, 'start_cluster') as start:
            self.assertTrue(compute.ensure_cluster(Mock())['available'])
        start.assert_not_called()

    def test_partial_cluster_waits_for_start_and_rechecks_health(self):
        reporter = Mock()
        with patch.object(compute, 'cluster_status', side_effect=[
                {'available': False, 'unavailable_services': ['hive']}, {'available': True}]), \
             patch.object(compute, 'compose') as compose:
            self.assertTrue(compute.ensure_cluster(reporter)['available'])
        compose.assert_called_once_with('up', '-d', '--wait', '--wait-timeout', '540',
                                        timeout=600, on_line=reporter)
        self.assertIn('hive', reporter.call_args_list[0].args[0])

    def test_failed_start_stops_with_recovery_hint(self):
        with patch.object(compute, 'cluster_status', return_value={'available': False}), \
             patch.object(compute, 'compose', side_effect=ContractViolation(
                 code='CLUSTER_COMMAND_FAILED', message='hive is unhealthy')):
            with self.assertRaises(ContractViolation) as raised:
                compute.ensure_cluster(Mock())
        self.assertEqual(raised.exception.code, 'CLUSTER_NOT_READY')
        self.assertIn('hive is unhealthy', raised.exception.message)
        self.assertIn('staged', raised.exception.suggested_action)

    def test_start_success_is_rejected_if_services_are_still_unhealthy(self):
        with patch.object(compute, 'cluster_status', return_value={
                'available': False, 'unavailable_services': ['hive']}), \
             patch.object(compute, 'compose'):
            with self.assertRaises(ContractViolation) as raised:
                compute.ensure_cluster(Mock())
        self.assertIn('hive', raised.exception.message)

    def test_sync_checks_cluster_before_download(self):
        from quantlens import sync
        calls = []
        provider = SimpleNamespace(name='test', update_all=Mock(side_effect=lambda **kw:
            calls.append('download') or SimpleNamespace(outcomes=[])))
        with patch.object(compute, 'ensure_cluster', side_effect=lambda *a: calls.append('ready')), \
             patch('quantlens.spark_daily.run', side_effect=lambda *a, **kw:
                   calls.append('publish') or {'run_id': 'test'}), patch.object(sync.store, 'add'):
            sync.run_sync(provider, console=Mock())
        self.assertEqual(calls, ['ready', 'download', 'publish'])

    def test_cluster_failure_prevents_download_and_publication(self):
        from quantlens import sync, spark_daily
        failure = ContractViolation(code='CLUSTER_NOT_READY', message='hive')
        provider = Mock()
        with patch.object(compute, 'ensure_cluster', side_effect=failure), \
             patch('quantlens.ingest_hdfs.ingest') as ingest, \
             patch.object(compute, 'submit') as submit:
            with self.assertRaises(ContractViolation):
                sync.run_sync(provider, console=Mock())
            with self.assertRaises(ContractViolation):
                spark_daily.run(Mock(), mode='data')
        provider.update_all.assert_not_called()
        ingest.assert_not_called()
        submit.assert_not_called()

    def test_direct_ingestion_checks_cluster_before_mutating_data(self):
        from quantlens import ingest_hdfs
        with patch.object(compute, 'ensure_cluster', side_effect=ContractViolation(
                code='CLUSTER_NOT_READY', message='hive')), \
             patch.object(ingest_hdfs, 'make_run') as run, \
             patch.object(ingest_hdfs, 'HDFS') as hdfs:
            with self.assertRaises(ContractViolation):
                ingest_hdfs.ingest()
        run.assert_not_called()
        hdfs.assert_not_called()

    def test_remote_job_checks_cluster_before_starting_worker_or_submitting(self):
        with patch.dict(os.environ, QUANTLENS_COMPUTE_LOCAL='0'), \
             patch.object(compute, 'ensure_cluster', side_effect=ContractViolation(
                 code='CLUSTER_NOT_READY', message='hive')), \
             patch('quantlens.gpu.ensure_worker') as worker, \
             patch.object(compute, 'compose') as compose:
            with self.assertRaises(ContractViolation):
                compute.submit('spark_backtest')
        worker.assert_not_called()
        compose.assert_not_called()

    def test_explicit_local_compute_does_not_start_docker(self):
        with patch.dict(os.environ, QUANTLENS_COMPUTE_LOCAL='1'), \
             patch.object(compute, 'ensure_cluster') as ready, \
             patch('quantlens.gpu.ensure_worker'), \
             patch('subprocess.run', return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(compute.submit('smoke_cluster'), 0)
        ready.assert_not_called()

    def test_every_runtime_service_has_restart_policy_and_healthcheck(self):
        import yaml
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'docker/compose.yaml').read_text())
        for name in compute.CLUSTER_SERVICES:
            with self.subTest(service=name):
                service = config['services'][name]
                self.assertEqual(service['restart'], 'unless-stopped')
                self.assertTrue(service['healthcheck']['test'])


if __name__ == '__main__':
    unittest.main()
