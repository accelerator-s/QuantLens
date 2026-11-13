from decimal import Decimal

from exceptions import PortfolioContractError
from .models import Account
from .recommend import investable_nav, latest_close_row
from .store import PortfolioStore
from .trades import holdings_to_positions


def account_capital(account: Account) -> Decimal:
    _signal_date, close_row = latest_close_row()
    holdings = PortfolioStore().read_holdings(
        account.account_id, market=account.market, instrument_class=account.instrument_class
    )
    capital = investable_nav(account, holdings_to_positions(holdings), close_row)
    if capital <= 0:
        raise PortfolioContractError(
            code="portfolio.zero_capital",
            message=f"账户 {account.account_id} 可投资净值为 0，无法运行回测",
            blocking=True,
            suggested_action="请先录入持仓或调整账户现金。",
        )
    return capital
