"""Refresh CSMAR indices independently from portfolio and research automation."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import yaml
from paths import workspace


class BenchmarkDataScheduler:
    def __init__(self, manager):
        self.manager = manager
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.loop, name='csmar-index-update', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def loop(self):
        while not self.stop_event.wait(30):
            try:
                self.tick()
            except Exception:
                import logging
                logging.getLogger('quantlens').exception('CSMAR index scheduler failed')

    def tick(self):
        from market_rules.markets.cn_a.calendar import ChinaAShareCalendar
        from providers.markets.cn_a.csmar.manifest import read_manifest
        from providers.markets.cn_a.csmar.incremental import compute_update_window
        path = workspace().config / 'benchmark_data.yaml'
        config = yaml.safe_load(path.read_text())
        if not config['enabled'] or config['source'] != 'csmar':
            return
        now = datetime.now(ZoneInfo(config['timezone']))
        day = now.date().isoformat()
        if now.strftime('%H:%M') < config['daily_time'] or not ChinaAShareCalendar().is_trading_day(now.date()):
            return
        state_path = workspace().experience / 'benchmark_data_schedule.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if state.get('last_attempt') == day or read_manifest().last_refresh_date_for('index_daily', 'fast') >= day:
            return
        windows = [window for table in ['index_basic', 'index_daily'] if (window := compute_update_window(table))]
        if not windows:
            return
        try:
            self.manager.start('CSMAR 指数数据更新', lambda report: self.update(windows, report), resource='data', category='data_sync')
        except Exception:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        pending = state_path.with_suffix('.pending')
        pending.write_text(json.dumps({'last_attempt': day, 'source': 'csmar'}))
        pending.replace(state_path)

    @staticmethod
    def update(windows, report):
        from providers.markets.cn_a.csmar.incremental import run_update_window
        results = []
        for window in windows:
            report(f'更新 {window.table}：{window.start_date} 至 {window.end_date}')
            result = run_update_window(window)
            results.append({'table': window.table, 'rows': result.downloaded_rows})
        return {'source': 'csmar', 'tables': results}
