from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from market_rules import RuleEngine
from panel.universe import _ST_STATUS_CODES

def board_of(instrument: str) -> str:
    
    if instrument.startswith(("688", "689")):
        return "科创板"
    if instrument.startswith("30"):
        return "创业板"
    if instrument.startswith(("43", "83", "87", "92")):
        return "北交所"
    return "主板"

def special_condition_of(board: str, is_st: bool) -> str:
    
    if board == "主板":
        return "ST" if is_st else ""
    return f"{board}ST" if is_st else board

def _dec_price(value: float) -> Decimal:
    
    return Decimal(f"{value:.2f}")

@dataclass
class Tradability:

    suspended: pd.DataFrame

    one_line_limit_up: pd.DataFrame

    one_line_limit_down: pd.DataFrame

    last_trading_date: pd.Series

    boards: pd.Series

    is_st: pd.DataFrame

def build_tradability(
    market_panel: dict[str, pd.DataFrame],
    rule_engine: RuleEngine,
    *,
    is_trading_full: pd.DataFrame,
) -> Tradability:
    
    close = market_panel["close"]
    high = market_panel["high"]
    low = market_panel["low"]
    is_trading = market_panel["is_trading"].astype(bool)
    listed = market_panel["listed_days"] > 0
    trading_status = market_panel["trading_status"]

    is_st = trading_status.isin(list(_ST_STATUS_CODES)).astype(bool)

    full = is_trading_full.astype(bool)
    dates = full.index.to_numpy()
    arr = full.to_numpy()
    last_idx = np.where(arr.any(axis=0), arr.shape[0] - 1 - np.argmax(arr[::-1], axis=0), -1)
    last_trading_date = pd.Series(
        [dates[i] if i >= 0 else pd.NaT for i in last_idx], index=full.columns
    )

    delisted = pd.DataFrame(
        close.index.to_numpy()[:, None] > last_trading_date.to_numpy()[None, :],
        index=close.index,
        columns=close.columns,
    )
    suspended = (~is_trading) & listed & (~delisted)

    prev_close = close.ffill().shift(1)
    flat = is_trading & (high == low) & high.notna()
    boards = pd.Series({inst: board_of(str(inst)) for inst in close.columns})

    up = pd.DataFrame(False, index=close.index, columns=close.columns)
    down = pd.DataFrame(False, index=close.index, columns=close.columns)
    rows, cols = np.where(flat.to_numpy())
    for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
        pc = prev_close.iat[r, c]
        if not np.isfinite(pc):
            continue  
        inst = close.columns[c]
        cond = special_condition_of(boards[inst], bool(is_st.iat[r, c]))
        lower, upper = rule_engine.price_limit_range(_dec_price(float(pc)), cond)
        px = _dec_price(float(high.iat[r, c]))
        if px >= upper:
            up.iat[r, c] = True
        elif px <= lower:
            down.iat[r, c] = True

    return Tradability(
        suspended=suspended,
        one_line_limit_up=up,
        one_line_limit_down=down,
        last_trading_date=last_trading_date,
        boards=boards,
        is_st=is_st,
    )

__all__ = ["Tradability", "board_of", "build_tradability", "special_condition_of"]
