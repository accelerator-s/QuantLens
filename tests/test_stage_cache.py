"""Cache hits must preserve input identity and never bless incomplete results."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from quantlens.stage_cache import StageCache, specification


class StageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = {'run_id': 'first', 'data_snapshot': 'data-v1', 'pipeline_code_hash': 'code-v1'}
        self.addCleanup(patch.stopall)
        patch('quantlens.stage_cache.configuration', return_value='config-v1').start()
        patch('quantlens.gpu.shared_root', return_value=self.root).start()
        patch('quantlens.gpu.use_gpu', return_value=True).start()
        patch('quantlens.stage_cache.specification', return_value=(['source'], ['result'])).start()
        self.state = {'source': 'input-v1', 'result': 'output-v1'}
        self.fingerprint = patch.object(StageCache, 'fingerprint', side_effect=lambda spec: self.state[spec]).start()
        self.action = Mock()

    def run_cache(self, context=None, **parameters):
        return StageCache(Mock(), context or self.context).run('weight_fit', self.action, **parameters)

    def test_identical_job_reuses_output_and_keeps_original_run(self):
        self.assertFalse(self.run_cache())
        self.assertTrue(self.run_cache({**self.context, 'run_id': 'second'}))
        self.action.assert_called_once()
        records = list((self.root / 'stage_cache').glob('*.json'))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())['run_id'], 'first')

    def test_changed_inputs_code_snapshot_parameters_config_or_policy_miss(self):
        self.run_cache()
        for field in ('data_snapshot', 'pipeline_code_hash'):
            self.assertFalse(self.run_cache({**self.context, field: 'changed'}))
        self.assertFalse(self.run_cache(start='2026-01-01'))
        self.state['source'] = 'input-v2'
        self.assertFalse(self.run_cache())
        self.state['source'] = 'input-v1'
        with patch('quantlens.stage_cache.configuration', return_value='config-v2'):
            self.assertFalse(self.run_cache())
        with patch('quantlens.gpu.use_gpu', return_value=False):
            self.assertTrue(self.run_cache())
        with patch.dict(os.environ, QUANTLENS_TRAIN_DEVICE='cpu'):
            self.assertFalse(self.run_cache())

    def test_missing_or_replaced_output_is_recomputed(self):
        self.run_cache()
        self.state['result'] = None
        self.assertFalse(self.run_cache())
        self.state['result'] = 'replacement'
        self.assertFalse(self.run_cache())
        self.assertTrue(self.run_cache())

    def test_failed_stage_has_no_reusable_completion_record(self):
        self.action.side_effect = RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            self.run_cache()
        self.assertFalse((self.root / 'stage_cache').exists())
        self.action.side_effect = None
        self.assertFalse(self.run_cache())
        self.assertTrue(self.run_cache())

    def test_retry_reuses_completed_stage_but_runs_failed_stage(self):
        cache = StageCache(Mock(), self.context)
        cache.run('weight_fit', self.action)
        failed = Mock(side_effect=RuntimeError('interrupted'))
        with self.assertRaises(RuntimeError):
            cache.run('kmeans', failed)
        self.assertTrue(self.run_cache())
        self.assertFalse(StageCache(Mock(), self.context).run('kmeans', self.action))

    def test_corrupt_record_is_ignored(self):
        self.run_cache()
        path = next((self.root / 'stage_cache').glob('*.json'))
        for invalid in ('broken', '[]', '{"key": "wrong"}'):
            path.write_text(invalid)
            self.assertFalse(self.run_cache())

    def test_repeated_batch_skips_compute_but_keeps_quality_and_publication(self):
        from quantlens import spark_batch
        from quantlens import (spark_dwd_prices, spark_dwd_fundamentals, spark_dwd_analyst,
            spark_dwd_industry, spark_dwd_label, spark_quality, sync_factor_meta,
            spark_factor_values, spark_factor_eval, spark_weight_fit, spark_kmeans,
            spark_fpgrowth, spark_pca, spark_engine, spark_portfolio, publish_hbase,
            spark_audit, spark_ads)
        # Use the real stage specifications with stable published fixtures.
        self.fingerprint.side_effect = None
        self.fingerprint.return_value = 'published-fixture'
        modules = [spark_dwd_prices, spark_dwd_fundamentals, spark_dwd_analyst,
            spark_dwd_industry, spark_dwd_label, spark_quality, sync_factor_meta,
            spark_factor_values, spark_factor_eval, spark_weight_fit, spark_kmeans,
            spark_fpgrowth, spark_pca, spark_engine, spark_portfolio, publish_hbase, spark_audit]
        calls = []
        with ExitStack() as stack:
            for module in modules:
                stack.enter_context(patch.object(module, 'run', side_effect=lambda *a, _name=module.__name__, **kw: calls.append(_name.rsplit('.', 1)[-1])))
            stack.enter_context(patch.object(spark_ads, 'export'))
            stack.enter_context(patch('quantlens.stage_cache.specification', side_effect=specification))
            spark = Mock()
            spark.version = 'test-spark'
            spark_batch.run(spark, self.context, 'first')
            calls.clear()
            spark_batch.run(spark, {**self.context, 'run_id': 'second'}, 'second')
        self.assertEqual(calls, ['spark_quality', 'sync_factor_meta', 'spark_portfolio',
                                 'publish_hbase', 'spark_audit'])


class SparkCacheTests(unittest.TestCase):
    def test_real_publication_hit_and_replaced_partition_miss(self):
        from datetime import date
        from quantlens.warehouse import session, make_run, lineage, publish
        from quantlens.stage_cache import table
        from pyspark.sql import functions as F
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,
                QUANTLENS_HOME=temp, QUANTLENS_COMPUTE_LOCAL='1',
                SPARK_LOCAL_IP='127.0.0.1', PYSPARK_PYTHON=sys.executable):
            spark = session('stage_cache_contract')
            spark.sparkContext.setLogLevel('ERROR')
            try:
                context = make_run('cache_fixture', 'synthetic-cache-only')
                rows = [('000001', date(2026, 1, 1), 'raw', 'v1', 2026, 1.)]
                frame = spark.createDataFrame(rows, 'code string,date date,factor string,version string,year int,value double')
                publish(spark, lineage(frame, context), 'dws.factor_values', ['factor', 'version', 'year'], run=context)
                inputs = [table('dws.factor_values', factors=['raw'])]
                outputs = [table('dws.style_cluster')]
                calls = []
                def fit():
                    calls.append(True)
                    publish(spark, lineage(spark.createDataFrame([(1,)], ['cluster_id']), context),
                            'dws.style_cluster', run=context)
                with patch('quantlens.stage_cache.specification', return_value=(inputs, outputs)):
                    cache = StageCache(spark, context)
                    self.assertFalse(cache.run('kmeans', fit))
                    self.assertTrue(StageCache(spark, context).run('kmeans', fit))
                    # A tree factor shares the table but must not invalidate raw factors.
                    model = frame.withColumn('factor', F.lit('model_gbt'))
                    publish(spark, lineage(model, context), 'dws.factor_values', ['factor', 'version', 'year'], run=context)
                    self.assertTrue(StageCache(spark, context).run('kmeans', fit))
                    # An actual source partition replacement must retrigger the fit.
                    publish(spark, lineage(frame, context), 'dws.factor_values', ['factor', 'version', 'year'], run=context)
                    self.assertFalse(StageCache(spark, context).run('kmeans', fit))
                    self.assertEqual(len(calls), 2)
                # A different experiment's publication preserves the first one's cache.
                record = spark.createDataFrame([('pca', 0.5), ('gbt', 0.1)], ['experiment', 'score'])
                publish(spark, lineage(record, context), 'ads.model_comparison', run=context)
                spec = table('ads.model_comparison', where="experiment='pca'", content=True)
                before = StageCache(spark, context).fingerprint(spec)
                record = spark.createDataFrame([('pca', 0.5), ('gbt', 0.9)], ['experiment', 'score'])
                publish(spark, lineage(record, {**context, 'run_id': 'another'}), 'ads.model_comparison', run={**context, 'run_id': 'another'})
                self.assertEqual(before, StageCache(spark, context).fingerprint(spec))
            finally:
                spark.stop()


if __name__ == '__main__':
    unittest.main()
