"""Exercise update cadence, empty checks, corrections and derived-cache reuse offline."""
import io
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from providers.markets.cn_a.csmar import incremental as inc
from providers.markets.cn_a.csmar.manifest import CSMARManifest

TODAY = date(2026, 11, 12)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 12, 18, tzinfo=timezone(timedelta(hours=8)))


class SyncPlanning(unittest.TestCase):
    def setUp(self):
        self.manifest = CSMARManifest()
        self.clock = patch.object(inc, 'datetime', Clock)
        self.clock.start()
        self.reader = patch.object(inc, 'read_manifest', return_value=self.manifest)
        self.reader.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.reader.stop)

    def seed(self, table, *, fast=0, deep=10, audit=100):
        self.manifest.last_dates[table] = TODAY.isoformat()
        self.manifest.last_refresh_dates[f'{table}:fast'] = (TODAY - timedelta(days=fast)).isoformat()
        self.manifest.last_refresh_dates[f'{table}:deep'] = (TODAY - timedelta(days=deep)).isoformat()
        self.manifest.last_audit_dates[table] = (TODAY - timedelta(days=audit)).isoformat()

    def test_matching_daily_tables_share_one_download_and_map_separately(self):
        raw = {f.field: [10.0] for name in ('prices_daily', 'returns_daily')
               for f in inc.get_table_config(name).fields}
        raw.update(Stkcd=['000001'], Trddt=['2026-11-11'], Trdsta=['1'])
        events, shared = [], {}
        with patch('providers.markets.cn_a.csmar.client.CSMARClient') as factory:
            client = factory.return_value.__enter__.return_value
            client.download.return_value = pd.DataFrame(raw)
            prices, _ = inc.download_table_full('prices_daily', '2026-11-05', '2026-11-11', shared_daily=shared)
            returns, info = inc.download_table_full('returns_daily', '2026-11-05', '2026-11-11',
                                                   shared_daily=shared, on_progress=events.append)
            client.download.assert_called_once()
            self.assertEqual(len(client.download.call_args.kwargs['fields']), 15)
            self.assertEqual(prices.loc[0, 'close'], 10)
            self.assertEqual(returns.loc[0, 'ret_wd'], 10)
            self.assertNotIn('close', returns.columns)
            self.assertEqual(info['duration_seconds'], 0)
            self.assertEqual(events[-1]['pack_stage'], 'reused')
            self.assertEqual(shared, {})

    def test_progress_counts_only_due_tables(self):
        from providers.markets.cn_a.csmar import orchestrator as orch
        window = inc.UpdateWindow('prices_daily', 'incremental', '2026-11-11', '2026-11-11', 1)
        plan = orch.AutoUpdatePlan(windows=[('prices_daily', window), ('returns_daily', None)],
                                   deferred_audits=[], skipped_unverified=[])
        stats = inc.BatchRunStats('prices_daily', 'incremental', window.start_date, window.end_date,
                                  batches_total=1, batches_ok=1)
        events = []
        with patch.object(orch, 'plan_auto_update', return_value=plan), \
             patch.object(orch, 'run_update_window', return_value=stats), \
             patch.object(orch, '_enrichment_due', return_value=False), \
             patch.object(orch, 'run_integrity_checks'):
            orch.run_auto_update(console=Console(file=io.StringIO()), on_progress=events.append)
        started = [e for e in events if e['state'] == 'table_started']
        self.assertEqual([(e['table_index'], e['table_total']) for e in started], [(1, 1)])

    def test_each_table_obeys_its_interval_and_keeps_overlap(self):
        for table, interval in inc.REFRESH_INTERVAL_DAYS.items():
            with self.subTest(table=table):
                self.seed(table, fast=interval-1, deep=40)
                self.assertIsNone(inc.compute_update_window(table))
                self.seed(table, fast=interval, deep=40)
                window = inc.compute_update_window(table)
                self.assertEqual(window.mode, 'refresh')
                self.assertEqual(window.start_date, (TODAY-timedelta(days=inc.FAST_REFRESH_DAYS[table])).isoformat())

    def test_initial_audit_and_deep_refresh_have_distinct_schedules(self):
        table = 'fs_income'
        self.assertEqual(inc.compute_update_window(table).mode, 'initial')
        self.seed(table, fast=10, deep=90, audit=200)
        self.assertEqual(inc.compute_update_window(table).mode, 'rolling')
        self.seed(table, fast=0, deep=0, audit=365)
        self.assertEqual(inc.compute_update_window(table).mode, 'audit')
        self.assertIsNone(inc.compute_update_window(table, skip_audit=True))
        self.assertEqual(inc.compute_update_window(table, force_audit=True).start_date, '2000-01-01')

    def test_full_audit_also_satisfies_recent_refresh(self):
        self.seed('fs_income', fast=80, deep=100, audit=0)
        self.assertIsNone(inc.compute_update_window('fs_income'))

    def test_prices_and_returns_share_trading_cutoff(self):
        with patch.object(inc, 'csmar_latest_available_date', return_value=date(2026, 11, 11)):
            for table in ('prices_daily', 'returns_daily'):
                self.manifest.last_dates[table] = '2026-11-10'
                window = inc.compute_update_window(table)
                self.assertEqual((window.mode, window.start_date, window.end_date),
                                 ('incremental', '2026-11-11', '2026-11-11'))
                self.manifest.last_dates[table] = '2026-11-11'
                self.assertIsNone(inc.compute_update_window(table))

    def test_empty_success_advances_check_but_partial_or_failure_does_not(self):
        window = inc.UpdateWindow('fs_income', 'refresh', '2026-02-01', TODAY.isoformat(), 1)
        stats = inc.BatchRunStats('fs_income', 'refresh', window.start_date, window.end_date,
                                  batches_total=1, batches_empty=1)
        with patch.object(inc, 'download_table_batched', return_value=stats), \
             patch.object(inc, 'record_refresh_complete') as record:
            inc.run_update_window(window)
            record.assert_called_once_with('fs_income', 'fast', TODAY.isoformat())
            record.reset_mock()
            inc.run_update_window(window, instrument_codes=['000001'])
            record.assert_not_called()
            stats.batches_empty = 0
            inc.run_update_window(window)
            record.assert_not_called()
        with patch.object(inc, 'download_table_batched', side_effect=RuntimeError('offline')), \
             patch.object(inc, 'record_refresh_complete') as record:
            with self.assertRaises(RuntimeError):
                inc.run_update_window(window)
            record.assert_not_called()

    def test_deep_success_records_both_refresh_tiers(self):
        window = inc.UpdateWindow('fs_income', 'initial', '2000-01-01', TODAY.isoformat(), 1)
        stats = inc.BatchRunStats('fs_income', 'initial', window.start_date, window.end_date,
                                  batches_total=1, batches_ok=1)
        with patch.object(inc, 'download_table_batched', return_value=stats), \
             patch.object(inc, 'record_refresh_complete') as refresh, \
             patch.object(inc, 'record_audit_complete') as audit:
            inc.run_update_window(window)
            self.assertEqual([c.args[1] for c in refresh.call_args_list], ['fast', 'deep'])
            audit.assert_called_once()

    def test_schedule_exposes_next_check(self):
        self.seed('top_shareholders', fast=3)
        self.assertEqual(inc.refresh_schedule('top_shareholders')['next_check'], '2026-11-23')

    def test_legacy_deep_check_survives_new_watermark_and_rejects_partial_run(self):
        from providers.markets.cn_a.csmar.manifest import DownloadRecord
        manifest = CSMARManifest(last_dates={'analyst_forecast':'2026-11-12'})
        for start, end in [('2024-08-18','2024-12-31'), ('2025-01-01','2025-12-31'),
                           ('2026-01-01','2026-08-18')]:
            manifest.downloads.append(DownloadRecord(table_name='analyst_forecast', start_date=start,
                end_date=end, mode='rolling', all_market=True, timestamp=datetime(2026,8,18,tzinfo=timezone.utc)))
        manifest.downloads.append(DownloadRecord(table_name='analyst_forecast', start_date='2024-11-04',
            end_date='2024-12-31', mode='rolling', all_market=True, timestamp=datetime(2026,11,4,tzinfo=timezone.utc)))
        self.assertEqual(manifest.last_refresh_date_for('analyst_forecast','deep'), '2026-08-18')


class StagedMerge(unittest.TestCase):
    def test_download_timestamp_alone_does_not_rewrite_business_data(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'fs_income.parquet'
            frame = pd.DataFrame({'instrument_id':['000001'], 'report_period':['2026-09-30'],
                                  'statement_type':['A'], 'revenue':[100.], 'downloaded_at':['old']})
            with patch.object(inc, '_staged_path', return_value=path):
                inc._merge_to_staged(frame, 'fs_income')
                stamp = path.stat().st_mtime_ns
                self.assertEqual(inc._merge_to_staged(frame.assign(downloaded_at='new'), 'fs_income'), (0, 0))
                self.assertEqual(path.stat().st_mtime_ns, stamp)
                self.assertEqual(inc._merge_to_staged(frame.assign(revenue=101., downloaded_at='new'), 'fs_income'), (0, 1))
                self.assertEqual(pd.read_parquet(path).downloaded_at.iloc[0], 'new')

    def test_correction_can_change_dtype_and_add_nullable_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'prices_daily.parquet'
            frame = pd.DataFrame({'instrument_id':['000001'], 'date':['2026-11-10'], 'close':[10]})
            with patch.object(inc, '_staged_path', return_value=path):
                inc._merge_to_staged(frame, 'prices_daily')
                self.assertEqual(inc._merge_to_staged(frame.assign(close=10.5), 'prices_daily'), (0, 1))
                actual = pd.read_parquet(path)
                self.assertEqual(actual.close.tolist(), [10.5])
                self.assertEqual(inc._merge_to_staged(actual.assign(volume=float('nan')), 'prices_daily'), (0, 1))
                self.assertIn('volume', pd.read_parquet(path).columns)

    def test_unchanged_rows_preserve_file_and_corrections_are_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'prices_daily.parquet'
            frame = pd.DataFrame({'instrument_id':['000001'], 'date':['2026-11-10'], 'close':[10.]})
            with patch.object(inc, '_staged_path', return_value=path):
                self.assertEqual(inc._merge_to_staged(frame, 'prices_daily'), (1, 1))
                enriched = pd.read_parquet(path).assign(quality='verified')
                enriched.to_parquet(path, index=False)
                stamp = path.stat().st_mtime_ns
                self.assertEqual(inc._merge_to_staged(frame, 'prices_daily'), (0, 0))
                self.assertEqual(path.stat().st_mtime_ns, stamp)
                corrected = frame.assign(close=11.)
                self.assertEqual(inc._merge_to_staged(corrected, 'prices_daily'), (0, 1))
                actual = pd.read_parquet(path)
                self.assertEqual(actual.close.tolist(), [11.])
                self.assertEqual(actual.quality.tolist(), ['verified'])
                fresh = frame.assign(instrument_id='000002', close=float('nan'))
                self.assertEqual(inc._merge_to_staged(fresh, 'prices_daily'), (1, 1))
                self.assertEqual(inc._merge_to_staged(fresh, 'prices_daily'), (0, 0))


class WarehouseSync(unittest.TestCase):

    def test_sync_submits_data_pipeline_without_research(self):
        from quantlens import sync
        console = Console(file=io.StringIO())
        dp = SimpleNamespace(name='test', update_all=lambda **kw: SimpleNamespace(outcomes=[]))
        with patch('quantlens.spark_daily.run',return_value={'run_id':'batch-test'}) as pipeline, \
             patch('quantlens.compute.ensure_cluster'), \
             patch.object(sync.store, 'add'):
            result=sync.run_sync(dp,console=console)
            pipeline.assert_called_once_with(console.print, mode='data')
            self.assertIn(('数据清洗与发布','done'),result.steps)



if __name__ == '__main__':
    unittest.main()
