"""Immutable account identity; fresh, explicit capital snapshots for each study."""
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from exceptions import ContractViolation


def account_snapshot(account_id, *, allow_zero=False):
    from portfolio.accounts import AccountRegistry
    from portfolio.store import PortfolioStore
    from portfolio.trades import holdings_to_positions
    from portfolio.recommend import investable_nav, latest_close_row
    from portfolio.transaction import lock
    from factor_evolution.diagnostics import digest
    with lock:
        account = AccountRegistry().get(account_id)
        if (account.market, account.instrument_class, account.reporting_currency) != ('CN-A', 'stock', 'CNY'):
            raise ContractViolation(code='STRATEGY_ACCOUNT_SCOPE', message='策略研究需要人民币 A 股股票账户。')
        holdings = PortfolioStore().read_holdings(account_id, market=account.market,
                                                 instrument_class=account.instrument_class)
        positions = holdings_to_positions(holdings)
        price_date, closes = latest_close_row() if positions else (None, pd.Series(dtype=float))
        capital = investable_nav(account, positions, closes)
        if not capital.is_finite() or capital < 0 or (capital == 0 and not allow_zero):
            raise ContractViolation(code='STRATEGY_ACCOUNT_CAPITAL', message='账户可投资资产必须大于零。')
        if positions and any(not Decimal(str(closes.get(code))).is_finite() or
                             Decimal(str(closes.get(code))) <= 0 for code, _ in positions):
            raise ContractViolation(code='STRATEGY_ACCOUNT_PRICE', message='账户持仓估值价格无效。')
        snapshot = {'account_id': account.account_id, 'alias': account.alias,
                    'capital': str(capital), 'star_market_enabled': account.star_market_enabled,
                    'valuation_date': price_date, 'captured_at': datetime.now(UTC).isoformat(),
                    'basis': 'available_cash_plus_marked_holdings',
                    'account_hash': digest(account.model_dump(mode='json')),
                    'holdings_hash': digest(holdings.to_dict('records'))}
        snapshot['snapshot_hash'] = digest(snapshot)
        return snapshot


def require_binding(profile, account_id):
    bound = profile.extra.get('account_binding', {}).get('account_id')
    if 'research_spec' in profile.extra and not bound:
        raise ContractViolation(code='STRATEGY_ACCOUNT_REQUIRED', message='策略缺少固定关联账户，不能执行。')
    if bound and bound != account_id:
        raise ContractViolation(code='STRATEGY_ACCOUNT_MISMATCH', message='策略只能用于创建时绑定的账户。')


def validate_binding_change(old, new):
    previous = old.get('extra', {}).get('account_binding')
    current = new.get('extra', {}).get('account_binding')
    if previous and previous != current:
        raise ContractViolation(code='STRATEGY_ACCOUNT_IMMUTABLE', message='策略关联账户创建后不可更换或移除。')
    spec = new.get('extra', {}).get('research_spec')
    if spec is not None and (not current or current.get('account_id') != spec.get('account_id')):
        raise ContractViolation(code='STRATEGY_ACCOUNT_REQUIRED', message='策略方案与固定关联账户不一致。')
    suspension = old.get('extra', {}).get('capital_suspension')
    if new.get('status') == 'active' and suspension:
        enabled = new.get('extra', {}).get('capital_manual_enable', {})
        if new['extra'].get('capital_suspension') or enabled.get('generation') != suspension['generation']:
            raise ContractViolation(code='STRATEGY_CAPITAL_MANUAL_ENABLE', message='资金跨档停用后需要人工重新启用。')
