"""Publish completed research outputs independently of their producer process."""
from __future__ import annotations

import logging
import threading
import time
import yaml

from quantlens import ads
from quantlens.backtest_publication import publication_lock, publish_completed_runs
from quantlens.compute import cluster_status
from paths import workspace


class ResearchResultPublisher:
    interval = 5
    category = 'backtest_publication'

    def __init__(self, manager):
        self.manager = manager
        self._stop = threading.Event()
        self._thread = None
        self._retry_after = 0.0
        self._published_revision = None
        self._published = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='quantlens-research-result-publisher', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def tick(self):
        active = self.manager.latest(category=self.category, running_only=True)
        if active or time.monotonic() < self._retry_after:
            return
        entry = ads.manifest().get('tables', {}).get('backtest_summary', {})
        revision = tuple((item['path'], item['sha256']) for item in entry.get('files', []))
        if revision != self._published_revision:
            self._published = {row['backtest_run'] for row in ads.query('backtest_summary', limit=10000)}
            self._published_revision = revision
        pending = []
        for status_path in sorted(workspace().runs_backtest.glob('*/*/status.yaml')):
            run_id = status_path.parent.name
            if run_id in self._published:
                continue
            status = yaml.safe_load(status_path.read_text()) or {}
            if status.get('state') != 'completed':
                continue
            pending.append(run_id)
        if not pending or not cluster_status()['available']:
            return
        def publish(reporter):
            try:
                with publication_lock():
                    # A primary research submitter may have finished its sync
                    # while this task waited for the publication lock.
                    current = {row['backtest_run'] for row in ads.query('backtest_summary', limit=10000)}
                    ready = [run_id for run_id in pending if run_id not in current]
                    if not ready:
                        return {'batch': None, 'run_ids': []}
                    reporter(f'发布 {len(ready)} 个已完成的研究回测')
                    batch = publish_completed_runs(ready, reporter=reporter)
            except Exception:
                self._retry_after = time.monotonic() + 60
                raise
            return {'batch': batch, 'run_ids': ready}

        self.manager.start('发布研究回测结果', publish, resource=self.category, category=self.category)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logging.getLogger(__name__).exception('研究回测结果发布检查失败')
            self._stop.wait(self.interval)
