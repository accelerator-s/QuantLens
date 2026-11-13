"""Account purchase eligibility, shared by recommendations and simulations."""
from backtest.tradability import board_of


def can_buy(code: str, *, star_market_enabled: bool) -> bool:
    return star_market_enabled or board_of(str(code)) != "科创板"
