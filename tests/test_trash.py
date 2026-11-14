"""Recoverable deletes and fixed 30-day retention in an isolated workspace."""
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from exceptions import QuantTrainingError
from factors.registry import FactorMetadata, FactorRegistry, FactorStatus
from paths import workspace
from strategy.models import StrategyProfileDoc
from strategy.store import StrategyProfileStore
from trash import TrashStore
from trash.retention import TrashCleanup, TrashRetention
from webui.backend.errors import install_error_handlers
from webui.backend.routes import factors, strategy, trash


class TrashTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {'QUANTLENS_HOME': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.ws = workspace()
        self.store = TrashStore()
        app = FastAPI()
        install_error_handlers(app)
        for router in (factors.router, strategy.router, trash.router):
            app.include_router(router, prefix='/api')
        self.client = TestClient(app)

    def factor(self, name='test_factor', code=True):
        factor = FactorMetadata(name=name, display_name='测试因子', assumption='完整恢复元数据',
                                plugin_path=f'factors/{name}.py' if code else '', parameters={'window': 17})
        registry = FactorRegistry()
        registry.upsert(factor)
        if code:
            path = self.ws.root / factor.plugin_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('def compute(frame):\n    return frame.close\n')
        return registry.get(name)

    def remove_factor(self, name='test_factor'):
        result = self.client.post(f'/api/factors/{name}/remove', json={'confirm': True})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['trash_bucket']

    def age(self, bucket, days):
        path = self.store._find_bucket(bucket) / 'meta.yaml'
        meta = yaml.safe_load(path.read_text())
        meta['trashed_at'] = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        path.write_text(yaml.safe_dump(meta))

    def test_factor_delete_and_restore_code_and_complete_metadata(self):
        original = self.factor()
        preview = self.client.post('/api/factors/test_factor/remove', json={}).json()
        self.assertTrue(preview['requires_confirm'])
        self.assertNotIn('说明', preview['preview'])
        self.assertEqual(FactorRegistry().get(original.name), original)
        bucket = self.remove_factor()
        self.assertFalse((self.ws.root / original.plugin_path).exists())
        self.assertEqual(FactorRegistry().list(), [])
        item = self.client.get('/api/trash').json()['items'][0]
        self.assertEqual(item['name'], '测试因子')
        self.assertEqual(datetime.fromisoformat(item['expires_at']) - datetime.fromisoformat(item['trashed_at']), timedelta(days=30))
        response = self.client.post(f'/api/trash/{bucket}/restore', json={'confirm': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(FactorRegistry().get(original.name), original)
        self.assertIn('def compute', (self.ws.root / original.plugin_path).read_text())
        self.assertEqual(self.store.list(), [])

    def test_invalid_factor_transitions_are_rejected(self):
        original = self.factor()
        for target in ('retired', 'unknown', '', 'ACTIVE', ' active ', None, 1, True, {}, []):
            for confirm in (False, True):
                with self.subTest(target=target, confirm=confirm):
                    result = self.client.post('/api/factors/test_factor/transition',
                                              json={'target': target, 'confirm': confirm})
                    self.assertEqual(result.status_code, 422, result.text)
                    self.assertEqual(FactorRegistry().get(original.name), original)
                    self.assertEqual(self.store.list(), [])

    def test_invalid_factor_status_filters_are_rejected(self):
        for status in ('retired', 'unknown', 'ACTIVE', ' active '):
            with self.subTest(status=status):
                result = self.client.get('/api/factors', params={'status': status})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual(result.json()['detail']['code'], 'WEBUI_INVALID_ENUM_VALUE')

    def test_factor_without_source_still_enters_trash_and_restores(self):
        original = self.factor(code=False)
        bucket = self.remove_factor()
        self.assertEqual(len(self.store.list()), 1)
        self.store.restore(bucket, confirm=True)
        self.assertEqual(FactorRegistry().get(original.name), original)

    def test_builtin_factor_delete_and_restore_preserves_implementation(self):
        source = self.ws.root / 'src' / 'quantlens' / 'spark_engine.py'
        source.parent.mkdir(parents=True)
        source.write_text('shared implementation')
        registry = FactorRegistry()
        registry.upsert(FactorMetadata(name='model_gbt', plugin_path='src/quantlens/spark_engine.py'))
        original = registry.get('model_gbt')
        bucket = self.remove_factor('model_gbt')
        self.assertEqual(source.read_text(), 'shared implementation')
        self.assertEqual(registry.list(), [])
        # Restoring the factor must not roll back a newer shared implementation.
        source.write_text('updated shared implementation')
        self.store.restore(bucket, confirm=True)
        self.assertEqual(registry.get('model_gbt'), original)
        self.assertEqual(source.read_text(), 'updated shared implementation')

    def test_shared_plugin_delete_preserves_other_factor(self):
        original = self.factor()
        registry = FactorRegistry()
        registry.upsert(FactorMetadata(name='another', plugin_path=original.plugin_path))
        bucket = self.remove_factor()
        self.assertTrue((self.ws.root / original.plugin_path).exists())
        self.assertEqual([factor.name for factor in registry.list()], ['another'])
        self.store.restore(bucket, confirm=True)
        self.assertEqual(registry.get(original.name), original)

    def test_disabled_factor_archive_preserves_status_on_restore(self):
        self.factor()
        registry = FactorRegistry()
        registry.transition('test_factor', target=FactorStatus.DISABLED)
        bucket = self.remove_factor()
        path = self.store._find_bucket(bucket) / 'meta.yaml'
        meta = yaml.safe_load(path.read_text())
        self.assertEqual(meta['factor_metadata']['status'], 'disabled')
        self.store.restore(bucket, confirm=True)
        factor = registry.get('test_factor')
        self.assertEqual(factor.status.value, 'disabled')
        self.assertFalse(factor.production_permission)

    def test_restore_conflict_keeps_archive_and_current_factor(self):
        self.factor()
        bucket = self.remove_factor()
        replacement = self.factor(code=False)
        with self.assertRaises(QuantTrainingError):
            self.store.restore(bucket, confirm=True)
        self.assertTrue((self.store._find_bucket(bucket) / 'content').exists())
        self.assertEqual(FactorRegistry().get(replacement.name), replacement)

    def test_file_conflict_does_not_register_or_overwrite_factor(self):
        original = self.factor()
        bucket = self.remove_factor()
        source = self.ws.root / original.plugin_path
        source.write_text('existing')
        with self.assertRaises(QuantTrainingError):
            self.store.restore(bucket, confirm=True)
        self.assertEqual(source.read_text(), 'existing')
        self.assertEqual(FactorRegistry().list(), [])
        self.assertEqual(len(self.store.list()), 1)

    def test_strategy_delete_and_restore_rebuilds_catalog(self):
        store = StrategyProfileStore()
        store.upsert(StrategyProfileDoc(profile_name='sample', display_name='测试策略'))
        original = store.get('sample')
        self.assertEqual(self.client.post('/api/strategies/sample/delete', json={'confirm': True}).status_code, 404)
        store.delete('sample', confirm=True)
        self.assertEqual(store.list(), [])
        item = self.client.get('/api/trash').json()['items'][0]
        self.assertEqual(item['name'], '测试策略')
        self.store.restore(item['bucket'], confirm=True)
        self.assertEqual(store.get('sample'), original)
        self.assertIn('sample', yaml.safe_load((self.ws.strategies / '_meta.yaml').read_text())['profiles'])

    def test_research_strategy_uses_current_delete_endpoint(self):
        from strategy.research import result_path
        store = StrategyProfileStore()
        store.upsert(StrategyProfileDoc(profile_name='current', display_name='研究策略',
                                      extra={'research_spec': {}}))
        result = result_path('current')
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text('{}')
        response = self.client.delete('/api/strategies/current')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.list(), [])
        self.assertFalse(result.exists())
        self.assertEqual(self.store.list(), [])

    def test_fixed_retention_expires_only_entries_older_than_30_days(self):
        self.factor('old')
        old = self.remove_factor('old')
        self.age(old, 31)
        self.factor('new')
        new = self.remove_factor('new')
        self.age(new, 29)
        items = self.client.get('/api/trash').json()['items']
        self.assertEqual([item['bucket'] for item in items], [new])
        self.assertEqual(datetime.fromisoformat(items[0]['expires_at']) - datetime.fromisoformat(items[0]['trashed_at']), timedelta(days=30))

    def test_legacy_settings_cannot_disable_fixed_retention(self):
        self.factor()
        bucket = self.remove_factor()
        self.age(bucket, 31)
        (self.ws.config / 'trash.yaml').write_text('retention_days: null\n')
        self.assertEqual(TrashRetention().cleanup(), [bucket])
        self.assertEqual(self.store.list(), [])

    def test_retention_settings_api_has_been_removed(self):
        self.assertEqual(self.client.get('/api/settings/trash').status_code, 404)
        for value in (None, 7, 30, 365):
            with self.subTest(value=value):
                self.assertEqual(self.client.put('/api/settings/trash', json={'retention_days': value}).status_code, 404)

    def test_empty_and_purge_require_confirmation_and_remove_entries(self):
        self.factor()
        bucket = self.remove_factor()
        self.assertTrue(self.client.post('/api/trash/empty', json={}).json()['requires_confirm'])
        self.assertTrue(self.client.post(f'/api/trash/{bucket}/purge', json={}).json()['requires_confirm'])
        self.assertEqual(len(self.store.list()), 1)
        response = self.client.post(f'/api/trash/{bucket}/purge', json={'confirm': True})
        self.assertEqual(response.status_code, 200)
        self.factor('another')
        self.remove_factor('another')
        self.assertEqual(self.client.post('/api/trash/empty', json={'confirm': True}).json(), {'count': 1})
        self.assertEqual(self.client.get('/api/trash').json()['items'], [])

    def test_background_cleanup_runs_without_browser_or_daily_automation(self):
        self.factor()
        bucket = self.remove_factor()
        self.age(bucket, 31)
        cleanup = TrashCleanup()
        # Stop after its first sweep, without waiting for the periodic interval.
        cleanup._stop.wait = lambda _interval: cleanup._stop.set()
        cleanup._run()
        self.assertEqual(self.store.list(), [])

    def test_failed_factor_delete_rolls_back_source_and_archive(self):
        original = self.factor()
        with patch.object(FactorRegistry, 'remove', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.store.remove_factor(original.name)
        self.assertTrue((self.ws.root / original.plugin_path).exists())
        self.assertEqual(FactorRegistry().get(original.name), original)
        self.assertEqual(self.store.list(), [])


if __name__ == '__main__':
    unittest.main()
