from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import yaml

from exceptions import (
    PortfolioContractError,
    PortfolioInsufficientError,
    PortfolioReconcileError,
    QuantTrainingError,
)
from paths import workspace

from .adapters import InstrumentAdapter, get_adapter
from .models import HOLDING_COLUMNS, Account, Action, Side
from .store import PortfolioStore, _require_pandas

if TYPE_CHECKING:
    import pandas as pd

QUANT = Decimal("0.00000001")

Positions = dict[tuple[str, str], dict]

def holdings_to_positions(df: pd.DataFrame) -> Positions:
    
    positions: Positions = {}
    for _, row in df.iterrows():
        positions[(str(row["instrument_id"]), str(row["side"]))] = {
            "qty": Decimal(str(row["qty"])),
            "available_qty": Decimal(str(row["available_qty"])),
            "cost_price": Decimal(str(row["cost_price"])),
            "currency": str(row["currency"]),
            "as_of": str(row["as_of"]),
        }
    return positions

def positions_to_holdings_df(positions: Positions, *, account: Account) -> pd.DataFrame:
    
    pd = _require_pandas()
    rows = []
    for (instrument_id, side), p in sorted(positions.items()):
        rows.append(
            {
                "account_id": account.account_id,
                "market": account.market,
                "instrument_class": account.instrument_class,
                "instrument_id": instrument_id,
                "side": side,
                "qty": str(p["qty"]),
                "available_qty": str(p["available_qty"]),
                "cost_price": str(p["cost_price"]),
                "currency": p["currency"],
                "as_of": p["as_of"],
            }
        )
    return pd.DataFrame(rows, columns=list(HOLDING_COLUMNS))

def apply_trade(
    positions: Positions,
    *,
    instrument_id: str,
    side: Side,
    action: Action,
    qty: Decimal,
    price: Decimal,
    currency: str,
    as_of: str,
) -> None:
    
    key = (instrument_id, side.value)
    existing = positions.get(key)

    if existing is not None and existing["currency"] != currency:
        raise PortfolioContractError(
            code="portfolio.trade_currency_mismatch",
            message=(
                f"{instrument_id}({side.value}) 持仓币种 {existing['currency']} "
                f"与成交币种 {currency} 不一致"
            ),
            blocking=True,
            suggested_action="请核对成交币种；单账户内同一标的不允许混币种",
        )

    if action is Action.OPEN:
        if existing is not None:
            raise PortfolioContractError(
                code="portfolio.trade_position_exists",
                message=f"{instrument_id}({side.value}) 已有持仓，不能重复 open",
                blocking=True,
                suggested_action="加仓请使用 --action increase",
            )
        positions[key] = {
            "qty": qty,
            "available_qty": qty,
            "cost_price": price,
            "currency": currency,
            "as_of": as_of,
        }
        return

    if existing is None:
        raise PortfolioContractError(
            code="portfolio.trade_position_missing",
            message=f"{instrument_id}({side.value}) 无持仓，无法 {action.value}",
            blocking=True,
            suggested_action="新开仓请使用 --action open",
        )

    if action is Action.INCREASE:
        new_qty = existing["qty"] + qty
        new_cost = ((existing["qty"] * existing["cost_price"] + qty * price) / new_qty).quantize(
            QUANT
        )
        existing.update(qty=new_qty, available_qty=new_qty, cost_price=new_cost, as_of=as_of)
        return

    if action is Action.DECREASE:
        if qty > existing["qty"]:
            raise PortfolioInsufficientError(
                code="portfolio.trade_qty_insufficient",
                message=f"{instrument_id}({side.value}) 持仓 {existing['qty']}，不足以减仓 {qty}",
                blocking=True,
                suggested_action="请核对数量；清仓请使用 --action close",
            )
        new_qty = existing["qty"] - qty
        if new_qty == 0:
            del positions[key]
        else:
            existing.update(qty=new_qty, available_qty=new_qty, as_of=as_of)
        return

    if action is Action.CLOSE:
        if qty != existing["qty"]:
            raise PortfolioContractError(
                code="portfolio.trade_close_qty_mismatch",
                message=(
                    f"close 数量 {qty} 与 {instrument_id}({side.value}) "
                    f"持仓 {existing['qty']} 不一致"
                ),
                blocking=True,
                suggested_action="close 必须全额平仓；部分卖出请使用 --action decrease",
            )
        del positions[key]
        return

    raise PortfolioContractError(  
        code="portfolio.trade_action_invalid",
        message=f"非法 action：{action!r}",
        blocking=True,
    )

def refresh_available_qty(
    positions: Positions,
    trades_df: pd.DataFrame,
    *,
    adapter: InstrumentAdapter,
    as_of: date,
) -> None:
    
    for (instrument_id, side), p in positions.items():
        recent = (
            trades_df[
                (trades_df["instrument_id"].astype(str) == instrument_id)
                & (trades_df["side"].astype(str) == side)
            ]
            if not trades_df.empty
            else trades_df
        )
        p["available_qty"] = adapter.compute_available_qty(
            total_qty=p["qty"], recent_trades=recent, as_of=as_of
        )

def entry_apply_trade(
    positions: Positions,
    *,
    account: Account,
    instrument_id: str,
    side: Side,
    action: Action,
    qty: Decimal,
    price: Decimal,
    currency: str,
    as_of: str,
) -> None:
    
    pd = _require_pandas()
    adapter = get_adapter(account.market, account.instrument_class)
    adapter.validate_qty(qty, action=action, instrument_id=instrument_id)
    stamp=pd.Timestamp(as_of)
    exec_date=(stamp.tz_localize('Asia/Shanghai') if stamp.tzinfo is None else stamp.tz_convert('Asia/Shanghai')).date()

    since = datetime.combine(exec_date - timedelta(days=20), datetime.min.time())
    prior = PortfolioStore().read_trades(
        account.account_id,
        market=account.market,
        instrument_class=account.instrument_class,
        start=since,
    )
    if action in (Action.DECREASE, Action.CLOSE):
        
        refresh_available_qty(positions, prior, adapter=adapter, as_of=exec_date)
        existing = positions.get((instrument_id, side.value))
        if existing is not None and qty > existing["available_qty"]:
            raise PortfolioInsufficientError(
                code="portfolio.trade_qty_unavailable",
                message=(
                    f"{instrument_id}({side.value}) 可用 {existing['available_qty']}，"
                    f"不足以卖出 {qty}（T+ 冻结）"
                ),
                blocking=True,
                suggested_action="当日买入按 T+ 规则冻结，请在次一交易日后卖出，或核对成交时间。",
            )
    apply_trade(
        positions,
        instrument_id=instrument_id,
        side=side,
        action=action,
        qty=qty,
        price=price,
        currency=currency,
        as_of=as_of,
    )
    current = pd.DataFrame(
        [
            {
                "instrument_id": instrument_id,
                "side": side.value,
                "action": action.value,
                "qty": str(qty),
                "executed_at": as_of,
            }
        ]
    )
    full = pd.concat([prior, current], ignore_index=True) if not prior.empty else current
    refresh_available_qty(positions, full, adapter=adapter, as_of=exec_date)

def replay_trades(trades_df: pd.DataFrame) -> Positions:
    
    positions: Positions = {}
    if trades_df.empty:
        return positions
    for _, row in trades_df.sort_values("executed_at").iterrows():
        try:
            apply_trade(
                positions,
                instrument_id=str(row["instrument_id"]),
                side=Side(str(row["side"])),
                action=Action(str(row["action"])),
                qty=Decimal(str(row["qty"])),
                price=Decimal(str(row["price"])),
                currency=str(row["currency"]),
                as_of=str(row["executed_at"]),
            )
        except QuantTrainingError as exc:
            raise PortfolioReconcileError(
                code="portfolio.reconcile_replay_failed",
                message=(
                    f"重放成交 {row['trade_id']} 失败：{exc.message}；成交流水与持仓推导规则冲突"
                ),
                context={"trade_id": str(row["trade_id"])},
                blocking=True,
                can_open_issue=True,
                suggested_action=(
                    "请检查该笔成交是否录入有误；如需修正，"
                    "在持仓与成交页面提交审计补丁。"
                ),
            ) from exc
    return positions

def last_reinit_at(account: Account) -> datetime | None:
    
    path = (
        workspace().portfolio_account(account.market, account.instrument_class, account.account_id)
        / "reinit_log.yaml"
    )
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = list(raw.get("entries") or [])
    if not entries:
        return None
    last = entries[-1].get("at", "")
    try:
        value = datetime.fromisoformat(str(last))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)

__all__ = [
    "QUANT",
    "Positions",
    "apply_trade",
    "entry_apply_trade",
    "holdings_to_positions",
    "last_reinit_at",
    "positions_to_holdings_df",
    "refresh_available_qty",
    "replay_trades",
]
