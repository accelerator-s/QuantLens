from __future__ import annotations

import yaml
from fastapi import APIRouter

from portfolio.accounts import AccountRegistry, DEFAULT_ACCOUNT_ID
from portfolio.store import PortfolioStore

from ..serialize import df_records, json_safe

router = APIRouter(tags=["portfolio"])

def _account(account_id: str):
    return AccountRegistry().get(account_id)

@router.get("/accounts")
def list_accounts(market: str = "", instrument_class: str = "") -> dict:
    registry = AccountRegistry()
    items = registry.list(
        market=market or None,
        instrument_class=instrument_class or None,
    )
    return json_safe({"items": items, "default_account_id": DEFAULT_ACCOUNT_ID})

@router.get("/portfolio/{account_id}/holdings")
def get_holdings(account_id: str) -> dict:
    from strategy import StrategyProfileStore
    if any(profile.profile_name==account_id for profile in StrategyProfileStore().list()):
        from quantlens.holdings import target_holdings
        return target_holdings(account_id)
    account = _account(account_id)
    df = PortfolioStore().read_holdings(
        account_id,
        market=account.market,
        instrument_class=account.instrument_class,
    )
    return {
        "account": json_safe(account),
        "total_rows": len(df),
        "items": df_records(df),
    }

@router.get("/portfolio/{account_id}/trades")
def get_portfolio_trades(account_id: str, limit: int = 200, offset: int = 0) -> dict:
    account = _account(account_id)
    df = PortfolioStore().read_trades(
        account_id,
        market=account.market,
        instrument_class=account.instrument_class,
    )
    if "executed_at" in df.columns and not df.empty:
        df = df.sort_values("executed_at", ascending=False)
    return {
        "total_rows": len(df),
        "items": df_records(df, limit=limit, offset=offset),
    }

@router.get("/portfolio/{account_id}/snapshots")
def get_snapshots(account_id: str, limit: int = 20) -> dict:
    account = _account(account_id)
    store = PortfolioStore()
    files = store.list_snapshots(
        account_id,
        market=account.market,
        instrument_class=account.instrument_class,
        limit=limit,
    )
    items = []
    for path in files:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items.append(json_safe({"file": path.name, **raw}))
    return {"items": items}

@router.get("/portfolio/{account_id}/plans")
def get_plans(account_id: str) -> dict:
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
