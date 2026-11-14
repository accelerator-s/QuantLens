"""Latch all bound strategies off when an account changes capital band."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import yaml

from exceptions import ContractViolation
from paths import workspace
from portfolio.transaction import lock


def policy():
    path = workspace().config/'account_capital_policy.yaml'
    raw = yaml.safe_load(path.read_text()) if path.exists() else {'small_below': '50000', 'large_from': '500000'}
    small, large = Decimal(str(raw['small_below'])), Decimal(str(raw['large_from']))
    if not small.is_finite() or not large.is_finite() or not 0 < small < large:
        raise ValueError('账户资金分档阈值无效')
    return small, large


def band(capital):
    value = Decimal(str(capital))
    if not value.is_finite() or value < 0:
        raise ValueError('账户资金无效')
    small, large = policy()
    return 'small' if value < small else 'medium' if value < large else 'large'


def bound_documents(account_id):
    from strategy import StrategyProfileStore
    return [d for d in StrategyProfileStore().list() if
            d.extra.get('account_binding', {}).get('account_id') == account_id]


def state_path(account_id):
    from portfolio.accounts import AccountRegistry
    account = AccountRegistry().get(account_id)
    return workspace().portfolio_account(account.market, account.instrument_class, account_id)/'capital_guard.json'


def state(account_id):
    path = state_path(account_id)
    return json.loads(path.read_text()) if path.exists() else {}


def transaction_files(files):
    """Extend the same account transaction to include dependent strategy state."""
    from strategy import StrategyProfileStore
    store = StrategyProfileStore()
    ids = {d.extra.get('account_binding', {}).get('account_id') for d in store.list()}
    additions, accounts = [], []
    for ident in ids- {None}:
        path = state_path(ident)
        if any(path.parent == f.parent or path.parent in f.parents for f in files):
            accounts.append(ident)
            additions += [path, *[store._profile_path(d.profile_name) for d in bound_documents(ident)]]
    return list(dict.fromkeys([*files, *additions])), accounts


def check_account(account_id, *, reason='account_mutation'):
    from strategy import StrategyProfileStore, StrategyStatus
    from strategy.account_binding import account_snapshot
    with lock:
        docs = bound_documents(account_id)
        if not docs:
            return {}
        old = state(account_id)
        error = ''
        try:
            snapshot = account_snapshot(account_id, allow_zero=True)
            current_band = band(snapshot['capital'])
        except Exception as exc:
            # Never guess a NAV from a cash-only or stale-price substitute.
            snapshot, current_band, error = None, 'unavailable', str(exc)
        previous_band = old.get('band') or band(docs[0].extra['creation_account_snapshot']['capital'])
        changed = previous_band != current_band
        generation = old.get('generation', 0) + int(changed)
        event = {'from_band': previous_band, 'to_band': current_band, 'generation': generation,
                 'at': datetime.now(UTC).isoformat(), 'reason': reason,
                 'capital': snapshot['capital'] if snapshot else None, 'error': error}
        if changed:
            store = StrategyProfileStore()
            for doc in docs:
                doc.status = StrategyStatus.DISABLED
                doc.extra['capital_suspension'] = {**event, 'requires_manual_enable': True}
                doc.updated_at = datetime.now(UTC)
                store._save_doc(doc)
        record = {'band': current_band, 'generation': generation, 'snapshot': snapshot,
                  'last_checked': event['at'], 'last_transition': event if changed else old.get('last_transition')}
        path = state_path(account_id)
        temporary = path.with_suffix('.pending')
        temporary.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False))
        temporary.replace(path)
        return record


def assert_generation(account_id, generation):
    current = check_account(account_id, reason='research_checkpoint')
    if current.get('band') == 'unavailable' or current.get('generation', 0) != generation:
        raise ContractViolation(code='STRATEGY_CAPITAL_CHANGED',
            message='账户资金已跨档或估值不可用，本次研究停止；关联策略需人工重新启用。')


def check_all():
    from strategy import StrategyProfileStore
    ids = {d.extra.get('account_binding', {}).get('account_id') for d in StrategyProfileStore().list()}
    return {ident: check_account(ident, reason='valuation_monitor') for ident in ids-{None}}


def on_mutation(account_id):
    from portfolio.transaction import in_progress
    if not in_progress():
        check_account(account_id)


class CapitalMonitor:
    """Revalue bound accounts while the service runs, including price-only changes."""
    def __init__(self):
        import threading
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        import threading
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name='account-capital-monitor', daemon=True)
        self.thread.start()

    def _loop(self):
        import logging
        while not self.stop_event.is_set():
            try:
                check_all()
            except Exception:
                logging.getLogger(__name__).exception('账户资金监控失败')
            self.stop_event.wait(60)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
