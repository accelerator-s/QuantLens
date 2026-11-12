from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from exceptions import ContractViolation
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

@router.get("/portfolio/{account_id}/plan/{plan_id}")
def plan_detail(account_id: str, plan_id: str) -> dict:
    from portfolio.capital_compare import comparison_exists

    account = AccountRegistry().get(account_id)
    path, raw, bucket = PortfolioStore().find_plan(plan_id, primary_account=account_id)
    return json_safe(
        {
            "plan_id": plan_id,
            "bucket": bucket,
            "file": path.name,
            "plan": raw,
            "comparison_exists": comparison_exists(plan_id=plan_id, account=account),
        }
    )

class PlanReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    model: str = ""

@router.post("/portfolio/plan/{plan_id}/review")
def plan_review(plan_id: str, payload: PlanReviewPayload) -> dict:
    from portfolio.models import Plan
    from portfolio.plan_review import annotate_plan
    from portfolio.recommend import previous_plan_for

    account = AccountRegistry().get(payload.account)
    _path, raw, _bucket = PortfolioStore().find_plan(plan_id, primary_account=payload.account)
    plan = Plan.model_validate(raw)
    previous = previous_plan_for(account, plan.strategy_profile_ref, exclude_plan_id=plan_id)
    result = annotate_plan(
        plan,
        account=account,
        previous_plan=previous,
        model_name=payload.model.strip() or None,
    )
    return json_safe({"plan_id": plan_id, "review": result})

class CapitalComparePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    reference_capital: str = "1000000"
    model: str = ""

@router.post("/portfolio/plan/{plan_id}/capital-comparison")
def plan_capital_comparison_run(plan_id: str, payload: CapitalComparePayload) -> dict:
    from portfolio.capital_compare import run_capital_comparison

    raw_capital = str(payload.reference_capital).strip() or "1000000"
    try:
        reference = Decimal(raw_capital)
    except InvalidOperation as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_DECIMAL",
            message=f"reference_capital 不是合法数字：{payload.reference_capital!r}。",
        ) from exc
    account = AccountRegistry().get(payload.account)
    result = run_capital_comparison(
        plan_id=plan_id,
        account=account,
        reference_capital=reference,
        model_name=payload.model.strip() or None,
    )
    return json_safe(result)

@router.get("/portfolio/{account_id}/plan/{plan_id}/capital-comparison")
def plan_capital_comparison_get(account_id: str, plan_id: str) -> dict:
    from portfolio.capital_compare import load_comparison

    account = AccountRegistry().get(account_id)
    return json_safe(load_comparison(plan_id=plan_id, account=account))
