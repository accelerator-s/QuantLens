from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

@dataclass
class Position:

    market_value: Decimal
    shares: int

class Ledger:

    def __init__(self, initial_cash: Decimal) -> None:
        self.cash: Decimal = initial_cash
        self.positions: dict[str, Position] = {}

    @property
    def position_value(self) -> Decimal:
        return sum((p.market_value for p in self.positions.values()), Decimal("0"))

    @property
    def nav(self) -> Decimal:
        return self.cash + self.position_value

    def weight_of(self, instrument: str) -> Decimal:
        pos = self.positions.get(instrument)
        if pos is None:
            return Decimal("0")
        nav = self.nav
        return pos.market_value / nav if nav > 0 else Decimal("0")

    def drift(self, ret_row: pd.Series) -> None:
        
        for inst, pos in self.positions.items():
            r = ret_row.get(inst)
            if r is None or pd.isna(r):
                continue
            pos.market_value *= Decimal(1) + Decimal(str(float(r)))

    def apply_buy(self, instrument: str, *, shares: int, value: Decimal, cash_out: Decimal) -> None:
        
        self.cash -= cash_out
        pos = self.positions.get(instrument)
        if pos is None:
            self.positions[instrument] = Position(market_value=value, shares=shares)
        else:
            pos.market_value += value
            pos.shares += shares

    def apply_sell(
        self, instrument: str, *, shares: int, value_out: Decimal, cash_in: Decimal
    ) -> None:
        
        pos = self.positions[instrument]
        pos.market_value -= value_out
        pos.shares -= shares
        self.cash += cash_in
        if pos.shares <= 0:
            del self.positions[instrument]

    def writeoff(self, instrument: str, *, recovery_rate: Decimal) -> Decimal:
        
        pos = self.positions.pop(instrument)
        recovered = pos.market_value * recovery_rate
        self.cash += recovered
        return pos.market_value

    def snapshot_positions(self) -> list[dict]:
        
        nav = self.nav
        return [
            {
                "instrument": inst,
                "market_value": float(pos.market_value),
                "shares_nominal": pos.shares,
                "weight": float(pos.market_value / nav) if nav > 0 else 0.0,
            }
            for inst, pos in sorted(self.positions.items())
        ]

__all__ = ["Ledger", "Position"]
