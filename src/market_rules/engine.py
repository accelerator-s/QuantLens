from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from .models import (
    MarketRuleConfig,
    PriceLimitRule,
    TradeDirection,
)
from .sessions import MarketSession, TradingCalendar, build_sessions

if TYPE_CHECKING:
    pass

# 计算结果

@dataclass(frozen=True)
class PriceLimitResult:

    lower: Decimal
    upper: Decimal
    hit_limit: bool
    notes: str = ""

@dataclass(frozen=True)
class FeeBreakdownItem:

    name: str
    amount: Decimal
    notes: str = ""

@dataclass(frozen=True)
class FeeBreakdown:

    items: list[FeeBreakdownItem] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((i.amount for i in self.items), Decimal("0"))

# 引擎

class RuleEngine:

    def __init__(self, rule: MarketRuleConfig) -> None:
        self.rule = rule
        self._sessions: list[MarketSession] = build_sessions(rule)

    # -- 涨跌停 --

    def price_limit_range(
        self,
        prev_close: Decimal,
        special_condition: str = "",
        *,
        as_of: date | None = None,
        instrument: str = "",
    ) -> tuple[Decimal, Decimal]:
        
        pl = self.rule.price_limit
        if not pl.enabled:
            return Decimal("0"), Decimal("Infinity")

        pct = self._resolve_limit_pct(pl, special_condition)
        if as_of is not None and special_condition.startswith('创业板'):
            if as_of < date(2020, 8, 24):
                pct = Decimal('10')
            elif instrument in {'300216','300156','300090'}:
                # These three stocks retained 10% limits throughout their
                # existing delisting period after the 2020 reform.
                pct = Decimal('10')
        factor = pct / Decimal("100")
        upper = (prev_close * (1 + factor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        lower = (prev_close * (1 - factor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return lower, upper

    def check_price_limit(
        self,
        price: Decimal,
        prev_close: Decimal,
        special_condition: str = "",
    ) -> PriceLimitResult:
        
        lower, upper = self.price_limit_range(prev_close, special_condition)
        hit = price >= upper or price <= lower
        return PriceLimitResult(lower=lower, upper=upper, hit_limit=hit)

    # -- 费用 --

    def calculate_fees(
        self,
        amount: Decimal,
        direction: str,
        *,
        commission_override: Decimal | None = None,
        as_of: date | None = None,
        instrument: str = "",
    ) -> FeeBreakdown:
        
        items: list[FeeBreakdownItem] = []
        for fee in self.rule.fees.items:
            if not self._direction_matches(fee.direction, direction):
                continue
            rate = fee.rate
            if as_of is not None and fee.name == '过户费':
                if as_of < date(2015, 8, 1):
                    raise ValueError('历史过户费数据只覆盖2015-08-01及以后，请补充更早的分市场费率。')
                rate = (Decimal('0.00001') if as_of >= date(2022, 4, 29)
                        else Decimal('0.000025') if instrument.startswith(('43','83','87','92'))
                        else Decimal('0.00002'))
            if commission_override is not None and fee.name == "佣金":
                rate = commission_override
            raw = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if fee.min_amount is not None and raw < fee.min_amount:
                raw = fee.min_amount
            if fee.max_amount is not None and raw > fee.max_amount:
                raw = fee.max_amount
            items.append(FeeBreakdownItem(name=fee.name, amount=raw, notes=fee.notes))
        return FeeBreakdown(items=items)

    def calculate_taxes(self, amount: Decimal, direction: str, *, as_of: date | None = None) -> FeeBreakdown:
        
        items: list[FeeBreakdownItem] = []
        for tax in self.rule.taxes.items:
            if not self._direction_matches(tax.direction, direction):
                continue
            rate = tax.rate
            if as_of is not None and tax.name == '印花税':
                if as_of < date(2008, 9, 19):
                    raise ValueError('历史印花税数据只覆盖2008-09-19及以后，请补充更早费率。')
                rate = Decimal('0.0005') if as_of >= date(2023, 8, 28) else Decimal('0.001')
            raw = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            items.append(FeeBreakdownItem(name=tax.name, amount=raw, notes=tax.notes))
        return FeeBreakdown(items=items)

    def calculate_total_cost(
        self,
        amount: Decimal,
        direction: str,
        *,
        commission_override: Decimal | None = None,
        as_of: date | None = None,
        instrument: str = "",
    ) -> FeeBreakdown:
        
        fees = self.calculate_fees(amount, direction, commission_override=commission_override,
                                   as_of=as_of, instrument=instrument)
        taxes = self.calculate_taxes(amount, direction, as_of=as_of)
        return FeeBreakdown(items=[*fees.items, *taxes.items])

    # -- 交易单位 --

    def round_to_min_unit(self, quantity: int) -> int:
        
        unit = self.rule.min_unit
        if unit <= 0:
            return quantity
        return (quantity // unit) * unit

    def is_valid_quantity(self, quantity: int) -> bool:
        
        unit = self.rule.min_unit
        if unit <= 0:
            return True
        return quantity > 0 and quantity % unit == 0

    def resolve_min_unit(self, board: str = "") -> tuple[int, int]:
        
        if board:
            for qr in self.rule.quantity_rules:
                if qr.board == board:
                    return qr.min_unit, qr.increment
        return self.rule.min_unit, 0

    def is_valid_quantity_for_board(self, quantity: int, board: str = "") -> bool:
        
        min_u, incr = self.resolve_min_unit(board)
        if quantity < min_u:
            return False
        if incr == 0:
            return quantity % min_u == 0
        return (quantity - min_u) % incr == 0 or quantity % min_u == 0

    # -- 交易时段 --

    def is_trading_time(self, t: time) -> bool:
        
        return any(s.contains_time(t) for s in self._sessions)

    # -- 结算 --

    def get_settlement_date(self, trade_date: date, calendar: TradingCalendar) -> date:
        
        if self.rule.t_plus == 0:
            return trade_date
        d = trade_date
        for _ in range(self.rule.t_plus):
            d = calendar.next_trading_day(d)
        return d

    def can_sell_date(self, buy_date: date, calendar: TradingCalendar) -> date:
        
        return self.get_settlement_date(buy_date, calendar)

    # -- 订单校验 --

    def validate_order(
        self,
        price: Decimal,
        prev_close: Decimal,
        quantity: int,
        direction: str,
        order_time: time | None = None,
        special_condition: str = "",
        board: str = "",
    ) -> list[str]:
        
        errors: list[str] = []

        if board:
            if not self.is_valid_quantity_for_board(quantity, board):
                min_u, incr = self.resolve_min_unit(board)
                if incr == 0:
                    errors.append(f"交易数量 {quantity} 不是 {board} 最小单位 {min_u} 的整数倍")
                else:
                    errors.append(
                        f"交易数量 {quantity} 不符合 {board} 交易单位要求"
                        f"（首笔 {min_u} 股，之后可 {incr} 股递增）"
                    )
        elif not self.is_valid_quantity(quantity):
            errors.append(f"交易数量 {quantity} 不是最小单位 {self.rule.min_unit} 的整数倍")

        if self.rule.price_limit.enabled:
            lower, upper = self.price_limit_range(prev_close, special_condition)
            if price > upper:
                errors.append(f"委托价 {price} 超过涨停价 {upper}")
            if price < lower:
                errors.append(f"委托价 {price} 低于跌停价 {lower}")

        if order_time is not None and not self.is_trading_time(order_time):
            errors.append(f"委托时间 {order_time} 不在交易时段内")

        return errors

    # -- 内部 --

    @staticmethod
    def _resolve_limit_pct(pl: PriceLimitRule, condition: str) -> Decimal:
        
        if condition:
            for sr in pl.special_rules:
                if sr.condition == condition:
                    return sr.limit_pct
        return pl.default_pct

    @staticmethod
    def _direction_matches(fee_dir: TradeDirection, order_dir: str) -> bool:
        if fee_dir == TradeDirection.BOTH:
            return True
        return fee_dir.value == order_dir
