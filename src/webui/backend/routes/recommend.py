from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from portfolio.accounts import AccountRegistry
from portfolio.store import PortfolioStore

from ..serialize import json_safe

router = APIRouter(tags=["recommend"])

class RecommendPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    profile: str
    as_of: str = ""
    model: str = ""

@router.post("/portfolio/recommend")
def recommend_generate(payload: RecommendPayload) -> dict:
    from portfolio.recommend import generate_plan
    from strategy.store import StrategyProfileStore

    account = AccountRegistry().get(payload.account)
    resolved = StrategyProfileStore().resolve(payload.profile)
    outcome = generate_plan(
        account=account,
        profile=resolved,
        as_of=payload.as_of.strip() or None,
        run_ai=True,
        model_name=payload.model.strip() or None,
    )
    
    _path, raw, bucket = PortfolioStore().find_plan(
        outcome.plan.plan_id, primary_account=payload.account
    )
    return json_safe(
        {
            "plan_id": outcome.plan.plan_id,
            "bucket": bucket,
            "plan": raw,
            "signal_date": outcome.signal_date,
            "nav": str(outcome.nav),
            "cash_available": str(outcome.cash_available),
            "skipped": [
                {"instrument_id": s.instrument_id, "rank": s.rank, "reason": s.reason}
                for s in outcome.skipped
            ],
            "staled_plan_ids": outcome.staled_plan_ids,
            "ai_ok": outcome.ai_ok,
            "ai_error": outcome.ai_error,
        }
    )
