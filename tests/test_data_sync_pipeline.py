"""Keep data publication separate from research and preserve research provenance."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class DataSyncPipeline(unittest.TestCase):
    def batch(self, mode):
        from quantlens import spark_batch
        from quantlens import (spark_dwd_prices, spark_dwd_fundamentals, spark_dwd_analyst,
            spark_dwd_industry, spark_dwd_label, spark_quality, sync_factor_meta,
            spark_factor_values, spark_factor_eval, spark_weight_fit, spark_kmeans,
            spark_fpgrowth, spark_pca, spark_engine, spark_portfolio, publish_hbase,
            spark_audit, spark_ads)
        modules = [spark_dwd_prices, spark_dwd_fundamentals, spark_dwd_analyst,
            spark_dwd_industry, spark_dwd_label, spark_quality, sync_factor_meta,
            spark_factor_values, spark_factor_eval, spark_weight_fit, spark_kmeans,
            spark_fpgrowth, spark_pca, spark_engine, spark_portfolio, publish_hbase, spark_audit]
        calls = []
        with ExitStack() as stack:
            for module in modules:
                stack.enter_context(patch.object(module, 'run', side_effect=lambda *a, _module_name=module.__name__, **kw: calls.append((_module_name.rsplit('.', 1)[-1], kw))))
            exporter = stack.enter_context(patch.object(spark_ads, 'export'))
            spark = Mock()
            context = {'run_id': 'test'}
            spark_batch.run(spark, context, 'test-batch', mode=mode)
            return calls, exporter.call_args

    def test_data_mode_only_cleans_and_checks_source_data(self):
        calls, exported = self.batch('data')
        self.assertEqual([name for name, _ in calls], ['spark_dwd_prices',
            'spark_dwd_fundamentals', 'spark_dwd_analyst', 'spark_dwd_industry', 'spark_quality'])
        self.assertEqual(calls[-1][1], {'include_labels': False})
        self.assertEqual(exported.kwargs, {'data_only': True})

    def test_research_mode_keeps_fitting_and_model_comparisons(self):
        calls, exported = self.batch('research')
        self.assertIn(('spark_weight_fit', {}), calls)
        self.assertIn(('spark_engine', {}), calls)
        self.assertIn(('spark_factor_eval', {'name': 'model_gbt'}), calls)
        self.assertIn(('spark_portfolio', {}), calls)
        self.assertEqual(exported.kwargs, {})

    def test_host_data_mode_does_not_apply_research_conclusions(self):
        from quantlens import spark_daily
        with patch.object(spark_daily, 'make_run', return_value={'run_id': 'data-test'}), \
             patch('quantlens.ingest_hdfs.ingest') as ingest, \
             patch('quantlens.compute.ensure_cluster') as ready, \
             patch('quantlens.audit_export.export'), \
             patch('quantlens.compute.submit') as submit, \
             patch('quantlens.sync_ads.sync', return_value={'run_id': 'data-test'}), \
             patch('quantlens.integrations.ensure_hive_hbase'), \
             patch('quantlens.model_comparison.apply_conclusions') as conclusions:
            reporter = Mock()
            self.assertEqual(spark_daily.run(reporter, mode='data'), {'run_id': 'data-test'})
            ready.assert_called_once_with(reporter)
            ingest.assert_called_once_with(cluster_checked=True)
            submit.assert_called_once_with('spark_batch', '--batch', 'data-test', '--mode', 'data', reporter=reporter)
            conclusions.assert_not_called()

    def test_data_export_does_not_republish_research_tables(self):
        from datetime import date
        from quantlens import spark_ads
        spark = MagicMock()
        frame = MagicMock()
        frame.columns = []
        frame.where.return_value = frame
        frame.coalesce.return_value = frame
        frame.count.return_value = 5
        spark.table.return_value = frame
        context = {'run_id': 'data-test', 'data_snapshot': 'new'}
        with patch.object(spark_ads, 'build', return_value=date(2026, 11, 12)), \
             patch.object(spark_ads, 'root_uri', return_value='hdfs:///quantlens'), \
             patch.object(spark_ads, 'write_json'), \
             patch('quantlens.archive_ads.run') as archive, \
             patch('pyspark.sql.functions.col') as column, patch('pyspark.sql.functions.lit'), \
             patch('pyspark.sql.functions.date_sub'), patch('pyspark.sql.functions.date_format'):
            column.return_value.__ge__.return_value = True
            manifest = spark_ads.export(spark, context, 'data-test', data_only=True)
        self.assertEqual(set(manifest['tables']), {'overview', 'market', 'stock_prices',
            'stock_fundamentals', 'stock_analyst', 'stock_industry', 'screen', 'quality_report'})
        self.assertEqual(manifest['scope'], 'data')
        archive.assert_not_called()

    def test_data_sync_retains_research_files_dates_and_versions(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from quantlens.sync_ads import sync
        from quantlens import ads
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, QUANTLENS_HOME=temp):
            root = Path(temp) / 'data'
            research = root / 'ads/batches/research/factor_weights/part.parquet'
            research.parent.mkdir(parents=True)
            pq.write_table(pa.table({'weight': [0.6], 'factor_version': ['v3']}), research)
            old = {'run_id': 'research', 'data_snapshot': 'old-snapshot', 'data_as_of': '2026-11-11',
                'source_uri': 'hdfs:///research', 'rows': 1,
                'files': [{'path': 'batches/research/factor_weights/part.parquet'}]}
            path = root / 'ads_manifest.json'
            path.write_text(json.dumps({'tables': {'factor_weights': old}, 'run_id': 'research'}))
            doc = {'run_id': 'data', 'scope': 'data', 'data_snapshot': 'new-snapshot', 'data_as_of': '2026-11-12',
                'tables': {'market': {'source_uri': 'hdfs:///new-market', 'rows': 1, 'run_id': 'data',
                    'data_as_of': '2026-11-12', 'data_snapshot': 'new-snapshot'}}}
            downloaded = []
            def receive(source, destination):
                downloaded.append(source)
                target = root / destination.removeprefix('/data/')
                target.mkdir(parents=True)
                pq.write_table(pa.table({'date': ['2026-11-12'], 'value': [1.]}), target / 'part.parquet')
            hdfs = SimpleNamespace(read_json=lambda _: copy.deepcopy(doc), get=receive)
            with patch('quantlens.sync_ads.HDFS', return_value=hdfs):
                first = sync('data-batch')
                second = sync('data-batch')
            self.assertEqual(first, second)
            self.assertEqual(downloaded, ['hdfs:///new-market'])
            self.assertEqual(first['tables']['factor_weights'], old)
            self.assertEqual(ads.provenance('factor_weights')['data_as_of'], '2026-11-11')
            self.assertEqual(ads.provenance('factor_weights')['data_snapshot'], 'old-snapshot')
            self.assertEqual(ads.query('factor_weights')[0]['factor_version'], 'v3')
            self.assertEqual(ads.provenance('market')['data_as_of'], '2026-11-12')


if __name__ == '__main__':
    unittest.main()
