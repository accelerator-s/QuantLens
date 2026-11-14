from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from backtest.tradability import board_of
from exceptions import PortfolioContractError
from market_rules import MarketRuleRegistry, RuleEngine
from market_rules.markets.cn_a import ChinaAShareCalendar

from ..models import Action, Side

if TYPE_CHECKING:
    from datetime import date

    import pandas as pd

_QUANT = Decimal("0.00000001")

_BUY_ACTIONS = frozenset({Action.OPEN, Action.INCREASE})

@dataclass(frozen=True)
class CnACostBreakdown:

    fee: Decimal
    tax: Decimal
    other_cost: Decimal

class CnAStockAdapter:

    market = "CN-A"
    instrument_class = "stock"

    def __init__(self) -> None:
        self._engine = RuleEngine(MarketRuleRegistry().resolve(self.market, self.instrument_class))
        self._calendar = ChinaAShareCalendar()

    # -- T+1 可卖 --

    def compute_available_qty(
        self,
        *,
        total_qty: Decimal,
        recent_trades: pd.DataFrame,
        as_of: date,
    ) -> Decimal:
        
        import pandas as pd

        frozen = Decimal("0")
        if recent_trades is not None and not recent_trades.empty:
            for _, row in recent_trades.iterrows():
                if str(row["action"]) not in ("open", "increase"):
                    continue
                stamp=pd.Timestamp(row['executed_at'])
                exec_date=(stamp.tz_localize('Asia/Shanghai') if stamp.tzinfo is None else stamp.tz_convert('Asia/Shanghai')).date()
                if self._engine.can_sell_date(exec_date, self._calendar) > as_of:
                    frozen += Decimal(str(row["qty"]))
        available = total_qty - frozen
        return available if available > Decimal("0") else Decimal("0")

    # -- 费用 --

    def compute_costs(
        self,
        *,
        side: Side,
        action: Action,
        qty: Decimal,
        price: Decimal,
        currency: str,
    ) -> CnACostBreakdown:
        
        if currency != "CNY":
            raise PortfolioContractError(
                code="portfolio.currency_mismatch",
                message=f"CN-A 股票以 CNY 计价，收到 {currency!r}",
                context={"currency": currency, "market": self.market},
                blocking=True,
                suggested_action="请以 CNY 录入 CN-A 成交金额",
            )
        direction = "buy" if action in _BUY_ACTIONS else "sell"
        amount = qty * price
        fees = self._engine.calculate_fees(amount, direction)
        taxes = self._engine.calculate_taxes(amount, direction)
        return CnACostBreakdown(fee=fees.total, tax=taxes.total, other_cost=Decimal("0"))

    # -- 数量校验 --

    def validate_qty(self, qty: Decimal, *, action: Action, instrument_id: str) -> None:
        
        if qty <= 0 or qty != qty.to_integral_value():
            raise PortfolioContractError(
                code="portfolio.trade_qty_invalid",
                message=f"股票数量必须为正整数股，收到 {qty}",
                context={"instrument_id": instrument_id, "qty": str(qty)},
                blocking=True,
                suggested_action="请以正整数股数录入",
            )
        if action not in _BUY_ACTIONS:
            return
        board = board_of(instrument_id)
        quantity = int(qty)
        if not self._engine.is_valid_quantity_for_board(quantity, board):
            min_unit, increment = self._engine.resolve_min_unit(board)
            rule_text = (
                f"须为 {min_unit} 股整数倍"
                if increment == 0
                else f"每笔至少 {min_unit} 股，超出可 {increment} 股递增"
            )
            raise PortfolioContractError(
                code="portfolio.trade_lot_invalid",
                message=f"买入数量 {quantity} 不符合 {board} 交易单位要求（{rule_text}）",
                context={
                    "instrument_id": instrument_id,
                    "board": board,
                    "qty": quantity,
                    "min_unit": min_unit,
                    "increment": increment,
                },
                blocking=True,
                suggested_action=f"请按 {board} 规则调整买入数量；卖出侧允许零股",
            )

    # -- 成本价 --

    def update_cost_price(
        self,
        *,
        current_qty: Decimal,
        current_cost: Decimal,
        trade_action: Action,
        trade_qty: Decimal,
        trade_price: Decimal,
    ) -> Decimal:
        
        if trade_action in _BUY_ACTIONS:
            new_qty = current_qty + trade_qty
            return ((current_qty * current_cost + trade_qty * trade_price) / new_qty).quantize(
                _QUANT
            )
        return current_cost

    # -- 现货常量 --

    def contract_multiplier(self) -> Decimal:
        return Decimal("1")

    def margin_requirement(self, *, side: Side, qty: Decimal, price: Decimal) -> Decimal:
        return Decimal("0")

    def position_layout(self) -> Literal["spot_long_only", "long_short", "lockable"]:
        return "spot_long_only"

__all__ = ["CnACostBreakdown", "CnAStockAdapter"]
