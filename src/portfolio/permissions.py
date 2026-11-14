"""Account purchase eligibility, shared by recommendations and simulations."""
from backtest.tradability import board_of
from quantlens.security_master import is_a_share


def can_buy(code: str, *, star_market_enabled: bool) -> bool:
    return is_a_share(code) and (star_market_enabled or board_of(str(code)) != "科创板")
