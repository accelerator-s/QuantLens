from __future__ import annotations

import yaml
import pandas as pd
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Query

from portfolio.accounts import AccountRegistry
from portfolio.store import PortfolioStore
from portfolio.transaction import lock

from ..serialize import df_records, json_safe

router = APIRouter(tags=["portfolio"])

def _account(account_id: str):
    return AccountRegistry().get(account_id)

@router.get("/accounts")
def list_accounts(market: str = "", instrument_class: str = "") -> dict:
    with lock:
        registry = AccountRegistry()
        items = registry.list(
            market=market or None,
            instrument_class=instrument_class or None,
        )
        return json_safe({"items": items, "default_account_id": items[0].account_id if items else ""})

@router.get("/portfolio/{account_id}/holdings")
def get_holdings(account_id: str) -> dict:
    with lock:
        account = _account(account_id)
        df = PortfolioStore().read_holdings(
            account_id,
            market=account.market,
            instrument_class=account.instrument_class,
        )
        from portfolio.adapters import get_adapter
        from portfolio.trades import holdings_to_positions, positions_to_holdings_df, refresh_available_qty
        store = PortfolioStore()
        positions = holdings_to_positions(df)
        trades = store.read_trades(account_id, market=account.market, instrument_class=account.instrument_class)
        refresh_available_qty(positions, trades, adapter=get_adapter(account.market, account.instrument_class),
                              as_of=datetime.now(ZoneInfo("Asia/Shanghai")).date())
        df = positions_to_holdings_df(positions, account=account)
        items = df_records(df)
        for item in items:
            item["frozen_qty"] = str(Decimal(item["qty"]) - Decimal(item["available_qty"]))
        return {
            "account": json_safe(account),
            "total_rows": len(df),
            "items": items,
        }

@router.get("/portfolio/{account_id}/trades")
def get_portfolio_trades(account_id: str, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> dict:
    with lock:
        account = _account(account_id)
        store = PortfolioStore()
        df = store.read_trades(
            account_id,
            market=account.market,
            instrument_class=account.instrument_class,
            include_reversed=True,
        )
        ledger = store.read_trade_ledger(account_id, market=account.market, instrument_class=account.instrument_class)
        reversals = ledger.get("reversals", {})
        latest_trade_id = ""
        if "executed_at" in df.columns and not df.empty:
            df = df.sort_values("executed_at", key=lambda values: pd.to_datetime(values, utc=True, errors="coerce", format="mixed"),
                                kind="stable", na_position="first").iloc[::-1]
            active = df[~df["trade_id"].isin(reversals)]
            if not active.empty and active.iloc[0].trade_id in ledger.get("entries", {}):
                latest_trade_id = str(active.iloc[0].trade_id)
        items = df_records(df, limit=limit, offset=offset)
        for item in items:
            reversal = reversals.get(item["trade_id"], {})
            item["reversed_at"] = reversal.get("reversed_at", "")
            item["is_reversed"] = bool(reversal)
        return {
            "total_rows": len(df),
            "items": items,
            "latest_trade_id": latest_trade_id,
        }

@router.get("/portfolio/{account_id}/plans")
def get_plans(account_id: str) -> dict:
    with lock:
        account = _account(account_id)
        store = PortfolioStore()
        plans_dir = store.ws.portfolio_plans(account.market, account.instrument_class, account_id)
        items = []
        for state, pattern in (("active", "*.yaml"), ("archive", "*/*.yaml")):
            state_dir = plans_dir / state
            if not state_dir.exists():
                continue
            for path in sorted(state_dir.glob(pattern), reverse=True):
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                items.append(json_safe({"bucket": state, "file": path.name, **raw}))
        return {"items": items}
