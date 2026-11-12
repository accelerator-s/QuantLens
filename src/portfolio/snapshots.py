from __future__ import annotations

import secrets
from decimal import Decimal
from typing import TYPE_CHECKING

from fx import snapshot_rates

from .models import (
    Account,
    CurrencyTotals,
    ReportingTotals,
    Side,
    Snapshot,
    SnapshotMode,
    SnapshotPosition,
)
from .trades import QUANT

if TYPE_CHECKING:
    from datetime import datetime

    import pandas as pd

BaseRow = dict

def base_rows_from_holdings(df: pd.DataFrame) -> list[BaseRow]:
    
    return [
        {
            "instrument_id": str(row["instrument_id"]),
            "side": str(row["side"]),
            "qty": Decimal(str(row["qty"])),
            "available_qty": Decimal(str(row["available_qty"])),
            "cost_price": Decimal(str(row["cost_price"])),
            "currency": str(row["currency"]),
        }
        for _, row in df.iterrows()
    ]

def base_rows_from_snapshot(snap: Snapshot) -> list[BaseRow]:
    
    return [
        {
            "instrument_id": p.instrument_id,
            "side": p.side.value,
            "qty": p.qty,
            "available_qty": p.available_qty,
            "cost_price": p.cost_price,
            "currency": p.currency,
        }
        for p in snap.positions
    ]

def new_snapshot_id(now: datetime) -> str:
    return f"SNP-{now:%Y%m%d}-{now:%H%M%S}Z-{secrets.token_hex(3)}"

def build_snapshot(
    *,
    account: Account,
    mode: SnapshotMode,
    base_rows: list[BaseRow],
    prices: dict[str, Decimal],
    taken_at: datetime,
    note: str = "",
) -> Snapshot:
    
    rc = account.reporting_currency
    currencies = sorted(
        {r["currency"] for r in base_rows} | {c.currency for c in account.cash} | {rc}
    )
    rates = snapshot_rates(currencies, reporting_currency=rc)

    positions: list[SnapshotPosition] = []
    mv_by_cur: dict[str, Decimal] = {}
    pnl_by_cur: dict[str, Decimal] = {}
    for r in base_rows:
        cur = r["currency"]
        price = prices[r["instrument_id"]]
        qty = r["qty"]
        mv_native = (qty * price).quantize(QUANT)
        signed = Decimal("1") if r["side"] == Side.LONG.value else Decimal("-1")
        pnl_native = (signed * (price - r["cost_price"]) * qty).quantize(QUANT)
        rate = rates[cur]
        positions.append(
            SnapshotPosition(
                market=account.market,
                instrument_class=account.instrument_class,
                instrument_id=r["instrument_id"],
                side=Side(r["side"]),
                qty=qty,
                available_qty=r["available_qty"],
                cost_price=r["cost_price"],
                current_price=price,
                currency=cur,
                market_value_native=mv_native,
                market_value_reporting=(mv_native * rate).quantize(QUANT),
                pnl_native=pnl_native,
                pnl_reporting=(pnl_native * rate).quantize(QUANT),
            )
        )
        mv_by_cur[cur] = mv_by_cur.get(cur, Decimal("0")) + mv_native
        pnl_by_cur[cur] = pnl_by_cur.get(cur, Decimal("0")) + pnl_native

    cash_by_cur = {c.currency: c for c in account.cash}
    totals_by_currency: dict[str, CurrencyTotals] = {}
    for cur in currencies:
        cash = cash_by_cur.get(cur)
        cash_total = cash.total if cash else Decimal("0")
        cash_available = cash.available if cash else Decimal("0")
        mv = mv_by_cur.get(cur, Decimal("0"))
        pnl = pnl_by_cur.get(cur, Decimal("0"))
        if cash is None and mv == 0 and pnl == 0:
            continue
        totals_by_currency[cur] = CurrencyTotals(
            total_assets=(cash_total + mv).quantize(QUANT),
            available_cash=cash_available,
            floating_pnl=pnl,
        )

    total_mv = Decimal("0")
    total_assets = Decimal("0")
    available_cash = Decimal("0")
    floating_pnl = Decimal("0")
    for cur, totals in totals_by_currency.items():
        rate = rates[cur]
        total_assets += totals.total_assets * rate
        available_cash += totals.available_cash * rate
        floating_pnl += totals.floating_pnl * rate
    for cur, mv in mv_by_cur.items():
        total_mv += mv * rates[cur]

    return Snapshot(
        snapshot_id=new_snapshot_id(taken_at),
        account_id=account.account_id,
        mode=mode,
        taken_at=taken_at,
        reporting_currency=rc,
        fx_snapshot={f"{cur}_{rc}": str(rates[cur]) for cur in currencies if cur != rc},
        totals_reporting=ReportingTotals(
            total_assets=total_assets.quantize(QUANT),
            total_market_value=total_mv.quantize(QUANT),
            available_cash=available_cash.quantize(QUANT),
            floating_pnl=floating_pnl.quantize(QUANT),
        ),
        totals_by_currency=totals_by_currency,
        positions=positions,
        source="manual",
        reconcile_status="ok",
        reconcile_note=note,
    )

__all__ = [
    "BaseRow",
    "base_rows_from_holdings",
    "base_rows_from_snapshot",
    "build_snapshot",
    "new_snapshot_id",
]
