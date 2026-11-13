"""Daily updates reuse old years; historical repairs invalidate dependent years."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from quantlens.stage_cache import StageCache


class IncrementalPriceCache(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(temp.name)
        patch('quantlens.stage_cache.configuration', return_value='config').start()
        patch('quantlens.gpu.shared_root', return_value=self.root).start()
        patch('quantlens.gpu.use_gpu', return_value=False).start()
        self.spark = Mock(version='test')
        self.context = {'run_id': 'first', 'data_snapshot': 'day1', 'pipeline_code_hash': 'cleaning-v1'}
        self.inputs = {2025: 'old-year', 2026: 'current-year'}
        self.outputs = {2025: 'published-old', 2026: 'published-current'}
        def fingerprint(spec):
            values = self.inputs if spec['table'].startswith('ods.') else self.outputs
            return [(year, value) for year, value in sorted(values.items())
                    if (spec.get('year_min') is None or year >= spec['year_min'])
                    and (spec.get('year_max') is None or year <= spec['year_max'])] or None
        patch.object(StageCache, 'fingerprint', side_effect=fingerprint).start()
        self.actions = []

    def run_years(self, snapshot='day1'):
        cache = StageCache(self.spark, {**self.context, 'data_snapshot': snapshot})
        return [cache.run('dwd_prices_year', lambda year=year: self.actions.append(year), year=year)
                for year in [2025, 2026]]

    def test_daily_append_reuses_previous_year(self):
        self.assertEqual(self.run_years(), [False, False])
        self.inputs[2026] = 'new-day'
        self.assertEqual(self.run_years('day2'), [True, False])
        self.assertEqual(self.actions, [2025, 2026, 2026])

    def test_historical_repair_recomputes_subsequent_listing_ages(self):
        self.run_years()
        self.inputs[2025] = 'historical-calendar-repaired'
        self.assertEqual(self.run_years('day2'), [False, False])

    def test_unrelated_snapshot_change_does_not_reclean(self):
        self.run_years()
        self.assertEqual(self.run_years('only-financials-changed'), [True, True])

    def test_replaced_output_is_not_reused(self):
        self.run_years()
        self.outputs[2025] = 'replaced'
        self.assertEqual(self.run_years(), [False, True])

    def test_unrelated_code_changes_reuse_cleaning_but_cleaning_changes_do_not(self):
        self.run_years()
        self.context['pipeline_code_hash'] = 'changed-web-route'
        self.assertEqual(self.run_years(), [True, True])
        with patch('quantlens.stage_cache.cleaning_code', return_value='new-cleaning-logic'):
            self.assertEqual(self.run_years(), [False, False])

    def test_unchanged_financial_inputs_survive_new_market_snapshot(self):
        cache = StageCache(self.spark, self.context)
        action = Mock()
        self.assertFalse(cache.run('dwd_fundamentals', action))
        self.assertTrue(StageCache(self.spark, {**self.context, 'data_snapshot': 'day2'}).run('dwd_fundamentals', action))
        action.assert_called_once()


class IncrementalPriceSpark(unittest.TestCase):
    def test_real_partitions_and_incremental_results_match_full_history(self):
        from datetime import date
        from quantlens.warehouse import session, publish, make_run
        from quantlens.spark_dwd_prices import run, clean_prices
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,
                QUANTLENS_HOME=temp, QUANTLENS_COMPUTE_LOCAL='1',
                SPARK_LOCAL_IP='127.0.0.1', PYSPARK_PYTHON=sys.executable), \
             patch('quantlens.stage_cache.configuration', return_value='fixture'):
            spark = session('incremental_price_contract')
            spark.sparkContext.setLogLevel('ERROR')
            try:
                context = make_run('incremental', 'initial')
                def prices(dates):
                    return spark.createDataFrame([('a', day, 10., 11., 9., 10., 1.e6, 1.e7, day.year) for day in dates],
                        'instrument_id string,date date,open double,high double,low double,close double,volume double,amount double,year int')
                def returns(dates):
                    return spark.createDataFrame([('a', day, 10., 0., '1', day.year) for day in dates],
                        'instrument_id string,date date,adj_close_wd double,ret_wd double,trading_status string,year int')
                first = [date(2025, 12, 31), date(2026, 1, 2)]
                for name, frame in [('prices_daily', prices(first)), ('returns_daily', returns(first))]:
                    publish(spark, frame, 'ods.' + name, ['year'], run=context)
                run(spark, context)
                before = sorted(spark.table('dwd.prices_clean').inputFiles())
                old = [uri for uri in before if 'year=2025/' in uri]
                new_dates = [date(2026, 1, 2), date(2026, 1, 5)]
                changed = {**context, 'run_id': 'next-day', 'data_snapshot': 'updated'}
                for name, frame in [('prices_daily', prices(new_dates)), ('returns_daily', returns(new_dates))]:
                    publish(spark, frame, 'ods.' + name, ['year'], run=changed)
                run(spark, changed)
                after = sorted(spark.table('dwd.prices_clean').inputFiles())
                self.assertEqual([uri for uri in after if 'year=2025/' in uri], old)
                self.assertNotEqual([uri for uri in after if 'year=2026/' in uri],
                                    [uri for uri in before if 'year=2026/' in uri])
                all_dates = [first[0], *new_dates]
                full = clean_prices(prices(all_dates), returns(all_dates))
                columns = [name for name in full.columns if name not in {'run_id', 'data_snapshot', 'version'}]
                self.assertEqual(spark.table('dwd.prices_clean').select(*columns).orderBy('date').collect(),
                                 full.select(*columns).orderBy('date').collect())
                # Inserting a previously absent trading day in the old year shifts
                # listing ages in the next year, which must therefore be republished.
                repaired = [date(2025, 12, 30), first[0]]
                for name, frame in [('prices_daily', prices(repaired)), ('returns_daily', returns(repaired))]:
                    publish(spark, frame, 'ods.' + name, ['year'], run=changed)
                run(spark, {**changed, 'run_id': 'repair', 'data_snapshot': 'repair'})
                self.assertNotEqual(sorted(spark.table('dwd.prices_clean').inputFiles()), after)
                actual = spark.table('dwd.prices_clean').where("date='2026-01-05'").first()
                self.assertEqual(actual.listed_days, 4)
            finally:
                spark.stop()


if __name__ == '__main__':
    unittest.main()
