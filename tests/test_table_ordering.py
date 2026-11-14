from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def test_portfolio_pages_sort_actual_times_before_offset(monkeypatch):
    from webui.backend.routes import portfolio

    rows = pd.DataFrame([
        {'trade_id': 'old', 'executed_at': '2026-11-14T10:00:00+08:00'},
        {'trade_id': 'new', 'executed_at': '2026-11-14T03:00:00Z'},
        {'trade_id': 'missing', 'executed_at': None},
        {'trade_id': 'middle', 'executed_at': '2026-11-14T02:30:00Z'},
    ])
    store = SimpleNamespace(read_trades=lambda *args, **kwargs: rows.copy(),
                            read_trade_ledger=lambda *args, **kwargs: {'entries': {'new': {}}, 'reversals': {}})
    monkeypatch.setattr(portfolio, '_account', lambda _: SimpleNamespace(market='CN-A', instrument_class='stock'))
    monkeypatch.setattr(portfolio, 'PortfolioStore', lambda: store)
    first = portfolio.get_portfolio_trades('fixture', limit=2, offset=0)
    last = portfolio.get_portfolio_trades('fixture', limit=2, offset=2)
    assert [row['trade_id'] for row in first['items']] == ['new', 'middle']
    assert [row['trade_id'] for row in last['items']] == ['old', 'missing']
    assert first['latest_trade_id'] == 'new'
