from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from market_rules import RuleEngine

from .protocol import BacktestProtocol
from .tradability import Tradability, _dec_price

_CENT = Decimal("0.01")

@dataclass
class Order:

    instrument: str
    side: str  
    target_weight: Decimal | None = None
    amount: Decimal | None = None

    sell_all: bool = False
    note: str = ""

@dataclass
class Fill:

    date: pd.Timestamp
    instrument: str
    side: str
    status: str
    target_weight: float | None = None
    shares: int = 0
    anchor_price: float | None = None
    exec_price: float | None = None
    amount: float = 0.0
    commission: float = 0.0
    transfer_fee: float = 0.0
    stamp_tax: float = 0.0
    other_fees: float = 0.0
    slippage_cost: float = 0.0
    note: str = ""

@dataclass
class DayResult:

    fills: list[Fill] = field(default_factory=list)
    pending_sells: dict[str, Order] = field(default_factory=dict)
    buy_amount: Decimal = Decimal("0")
    sell_amount: Decimal = Decimal("0")
    cost_paid: Decimal = Decimal("0")

def _fee_fields(breakdown) -> tuple[Decimal, Decimal, Decimal, Decimal]:  
    
    commission = transfer = stamp = other = Decimal("0")
    for item in breakdown.items:
        if item.name == "佣金":
            commission += item.amount
        elif item.name == "过户费":
            transfer += item.amount
        elif item.name == "印花税":
            stamp += item.amount
        else:
            other += item.amount
    return commission, transfer, stamp, other

def _slip(protocol: BacktestProtocol) -> Decimal:
    return protocol.slippage_bps / Decimal("10000")

# 卖出

def _fill_sell(
    ledger,  
    order: Order,
    *,
    date: pd.Timestamp,
    close: float,
    rule_engine: RuleEngine,
    protocol: BacktestProtocol,
    min_unit: int,
    slippage: Decimal,
) -> Fill | None:
    
    pos = ledger.positions[order.instrument]
    if order.sell_all:
        shares = pos.shares
    else:
        assert order.amount is not None
        frac = order.amount / pos.market_value if pos.market_value > 0 else Decimal(1)
        frac = min(frac, Decimal(1))
        shares = int(int(pos.shares * frac) // min_unit) * min_unit
        if shares <= 0:
            return None
        if shares >= pos.shares:
            shares = pos.shares
    frac = Decimal(shares) / Decimal(pos.shares)
    gross_value = pos.market_value * frac
    proceeds = gross_value * (Decimal(1) - slippage)
    proceeds_q = proceeds.quantize(_CENT, rounding=ROUND_HALF_UP)
    breakdown = rule_engine.calculate_total_cost(
        proceeds_q, "sell", commission_override=protocol.commission_override_rate
    )
    commission, transfer, stamp, other = _fee_fields(breakdown)
    cash_in = proceeds - breakdown.total
    slippage_cost = gross_value - proceeds

    ledger.apply_sell(order.instrument, shares=shares, value_out=gross_value, cash_in=cash_in)
    close_dec = _dec_price(close)
    return Fill(
        date=date,
        instrument=order.instrument,
        side="sell",
        status="filled",
        target_weight=float(order.target_weight) if order.target_weight is not None else None,
        shares=shares,
        anchor_price=float(close_dec),
        exec_price=float(close_dec * (Decimal(1) - slippage)),
        amount=float(proceeds),
        commission=float(commission),
        transfer_fee=float(transfer),
        stamp_tax=float(stamp),
        other_fees=float(other),
        slippage_cost=float(slippage_cost),
        note=order.note,
    )

def force_close(
    ledger,  
    instrument: str,
    *,
    date: pd.Timestamp,
    close: float,
    rule_engine: RuleEngine,
    protocol: BacktestProtocol,
) -> Fill:
    
    pos = ledger.positions[instrument]
    gross_value = pos.market_value
    gross_q = gross_value.quantize(_CENT, rounding=ROUND_HALF_UP)
    breakdown = rule_engine.calculate_total_cost(
        gross_q, "sell", commission_override=protocol.commission_override_rate
    )
    commission, transfer, stamp, other = _fee_fields(breakdown)
    cash_in = gross_value - breakdown.total
    ledger.apply_sell(instrument, shares=pos.shares, value_out=gross_value, cash_in=cash_in)
    close_dec = _dec_price(close)
    return Fill(
        date=date,
        instrument=instrument,
        side="sell",
        status="forced_delist_close",
        shares=0,
        anchor_price=float(close_dec),
        exec_price=float(close_dec),
        amount=float(gross_value),
        commission=float(commission),
        transfer_fee=float(transfer),
        stamp_tax=float(stamp),
        other_fees=float(other),
        note="最后可交易日强制平仓（退市处置）",
    )

# 买入

def _lot_round(shares_raw: int, min_unit: int, increment: int) -> int:
    
    if shares_raw < min_unit:
        return 0
    if increment <= 0:
        return (shares_raw // min_unit) * min_unit
    return min_unit + ((shares_raw - min_unit) // increment) * increment

def _fill_buy(
    ledger,  
    order: Order,
    *,
    date: pd.Timestamp,
    close: float,
    rule_engine: RuleEngine,
    protocol: BacktestProtocol,
    board: str,
    budget: Decimal,
    slippage: Decimal,
) -> tuple[Fill, Decimal]:
    
    assert order.amount is not None
    close_dec = _dec_price(close)
    exec_price = close_dec * (Decimal(1) + slippage)
    min_unit, increment = rule_engine.resolve_min_unit(board)

    alloc = min(order.amount, budget)
    shares = _lot_round(int(alloc / exec_price), min_unit, increment)
    if shares <= 0:
        return (
            Fill(
                date=date,
                instrument=order.instrument,
                side="buy",
                status="rejected_lot_unaffordable",
                target_weight=(
                    float(order.target_weight) if order.target_weight is not None else None
                ),
                anchor_price=float(close_dec),
                exec_price=float(exec_price),
                amount=float(order.amount),
                note=f"目标金额 {order.amount:.2f} 不足 {board} 一手（{min_unit} 股）",
            ),
            Decimal("0"),
        )

    total_out = Decimal("0")
    breakdown = None
    for _ in range(5):
        amount = Decimal(shares) * exec_price
        amount_q = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
        breakdown = rule_engine.calculate_total_cost(
            amount_q, "buy", commission_override=protocol.commission_override_rate
        )
        total_out = amount + breakdown.total
        if total_out <= budget:
            break
        shrunk = _lot_round(int((budget - breakdown.total) / exec_price), min_unit, increment)
        if shrunk >= shares:
            shrunk = shares - (increment if increment > 0 else min_unit)
        shares = shrunk
        if shares <= 0:
            break
    if shares <= 0 or breakdown is None or total_out > budget:
        return (
            Fill(
                date=date,
                instrument=order.instrument,
                side="buy",
                status="rejected_insufficient_cash",
                target_weight=(
                    float(order.target_weight) if order.target_weight is not None else None
                ),
                anchor_price=float(close_dec),
                exec_price=float(exec_price),
                amount=float(order.amount),
                note="预算（含费税）不足一手",
            ),
            Decimal("0"),
        )

    amount = Decimal(shares) * exec_price
    value = Decimal(shares) * close_dec
    commission, transfer, stamp, other = _fee_fields(breakdown)
    ledger.apply_buy(order.instrument, shares=shares, value=value, cash_out=total_out)
    return (
        Fill(
            date=date,
            instrument=order.instrument,
            side="buy",
            status="filled",
            target_weight=float(order.target_weight) if order.target_weight is not None else None,
            shares=shares,
            anchor_price=float(close_dec),
            exec_price=float(exec_price),
            amount=float(amount),
            commission=float(commission),
            transfer_fee=float(transfer),
            stamp_tax=float(stamp),
            other_fees=float(other),
            slippage_cost=float(amount - value),
            note=order.note,
        ),
        total_out,
    )

# 单日撮合编排

def execute_day(
    ledger,  
    *,
    date: pd.Timestamp,
    sells: list[Order],
    buys: list[Order],
    close_row: pd.Series,
    trad: Tradability,
    rule_engine: RuleEngine,
    protocol: BacktestProtocol,
    min_cash_ratio: Decimal,
) -> DayResult:
    
    result = DayResult()
    slippage = _slip(protocol)
    suspended_row = trad.suspended.loc[date]
    up_row = trad.one_line_limit_up.loc[date]
    down_row = trad.one_line_limit_down.loc[date]

    for order in sorted(sells, key=lambda o: o.instrument):
        inst = order.instrument
        if inst not in ledger.positions:
            continue  # 已被强平 / 核销
        if protocol.block_sell_on_suspension and bool(suspended_row.get(inst, False)):
            result.pending_sells[inst] = order
            result.fills.append(
                Fill(
                    date=date,
                    instrument=inst,
                    side="sell",
                    status="pending_suspension",
                    note="停牌，挂起次日重试",
                )
            )
            continue
        if protocol.block_sell_on_limit_down_one_line and bool(down_row.get(inst, False)):
            result.pending_sells[inst] = order
            result.fills.append(
                Fill(
                    date=date,
                    instrument=inst,
                    side="sell",
                    status="pending_limit_down",
                    note="一字跌停，挂起次日重试",
                )
            )
            continue
        close = close_row.get(inst)
        if close is None or pd.isna(close):
            result.pending_sells[inst] = order
            result.fills.append(
                Fill(
                    date=date,
                    instrument=inst,
                    side="sell",
                    status="pending_suspension",
                    note="当日无行情，挂起次日重试",
                )
            )
            continue
        min_unit, _ = rule_engine.resolve_min_unit(trad.boards[inst])
        fill = _fill_sell(
            ledger,
            order,
            date=date,
            close=float(close),
            rule_engine=rule_engine,
            protocol=protocol,
            min_unit=min_unit,
            slippage=slippage,
        )
        if fill is not None:
            result.fills.append(fill)
            result.sell_amount += Decimal(str(fill.amount))
            result.cost_paid += (
                Decimal(str(fill.commission))
                + Decimal(str(fill.transfer_fee))
                + Decimal(str(fill.stamp_tax))
                + Decimal(str(fill.other_fees))
                + Decimal(str(fill.slippage_cost))
            )

    executable: list[Order] = []
    for order in sorted(buys, key=lambda o: o.instrument):
        inst = order.instrument
        if protocol.block_buy_on_suspension and bool(suspended_row.get(inst, False)):
            result.fills.append(
                Fill(
                    date=date,
                    instrument=inst,
                    side="buy",
                    status="rejected_suspension",
                    target_weight=(
                        float(order.target_weight) if order.target_weight is not None else None
                    ),
                    note="执行日停牌，买单作废",
                )
            )
            continue
        if protocol.block_buy_on_limit_up_one_line and bool(up_row.get(inst, False)):
            result.fills.append(
                Fill(
                    date=date,
                    instrument=inst,
                    side="buy",
                    status="rejected_limit_up",
                    target_weight=(
                        float(order.target_weight) if order.target_weight is not None else None
                    ),
                    note="执行日一字涨停，买单作废",
                )
            )
            continue
        close = close_row.get(inst)
        if close is None or pd.isna(close):
            result.fills.append(
                Fill(
                    date=date,
                    instrument=inst,
                    side="buy",
                    status="rejected_suspension",
                    target_weight=(
                        float(order.target_weight) if order.target_weight is not None else None
                    ),
                    note="执行日无行情，买单作废",
                )
            )
            continue
        executable.append(order)

    available = ledger.cash - min_cash_ratio * ledger.nav
    if available < 0:
        available = Decimal("0")
    desired = sum((o.amount for o in executable if o.amount is not None), Decimal("0"))
    scale = available / desired if desired > available and desired > 0 else Decimal(1)

    remaining = available
    for order in executable:
        assert order.amount is not None
        scaled = Order(
            instrument=order.instrument,
            side="buy",
            target_weight=order.target_weight,
            amount=order.amount * scale,
            note=order.note,
        )
        fill, cash_out = _fill_buy(
            ledger,
            scaled,
            date=date,
            close=float(close_row[order.instrument]),
            rule_engine=rule_engine,
            protocol=protocol,
            board=trad.boards[order.instrument],
            budget=remaining,
            slippage=slippage,
        )
        result.fills.append(fill)
        remaining -= cash_out
        if fill.status == "filled":
            result.buy_amount += Decimal(str(fill.amount))
            result.cost_paid += (
                Decimal(str(fill.commission))
                + Decimal(str(fill.transfer_fee))
                + Decimal(str(fill.stamp_tax))
                + Decimal(str(fill.other_fees))
                + Decimal(str(fill.slippage_cost))
            )

    return result

__all__ = ["DayResult", "Fill", "Order", "execute_day", "force_close"]
