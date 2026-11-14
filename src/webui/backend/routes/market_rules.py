from __future__ import annotations
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter
from exceptions import ContractViolation
from market_rules import RuleEngine
from market_rules.models import MarketRuleStatus
from market_rules.registry import MarketRuleRegistry
from ..schemas import ConfirmPayload, TransitionPayload, confirm_preview, parse_enum
from ..serialize import json_safe
router = APIRouter(tags=["market-rules"])

@router.get("/market-rules")
def list_market_rules(status: str = "", market: str = "", instrument_class: str = "") -> dict:
    registry = MarketRuleRegistry()
    items = registry.list(
        status=parse_enum(MarketRuleStatus, status, "status") if status else None,
        market=market or None,
        instrument_class=instrument_class or None,
    )
    return {
        "items": [json_safe(r) for r in items],
        "statuses": [s.value for s in MarketRuleStatus],
    }

@router.get("/market-rules/{name}")
def get_market_rule(name: str) -> dict:
    return json_safe({"rule": MarketRuleRegistry().get(name)})

@router.post("/market-rules/{name}/transition")
def transition_market_rule(name: str, payload: TransitionPayload) -> dict:
    registry = MarketRuleRegistry()
    target = parse_enum(MarketRuleStatus, payload.target, "target")
    if not payload.confirm:
        rule = registry.get(name)
        return confirm_preview(
            {
                "操作": f"市场规则状态流转 -> {target.value}",
                "规则": rule.name,
                "当前状态": rule.status.value,
                "目标状态": target.value,
            }
        )
    rule = registry.transition(name, target=target, confirm=True)
    return json_safe({"rule": rule})

@router.post("/market-rules/{name}/remove")
def remove_market_rule(name: str, payload: ConfirmPayload) -> dict:
    registry = MarketRuleRegistry()
    rule = registry.get(name)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "删除市场规则（不可撤销）",
                "规则": rule.name,
                "市场": f"{rule.market} {rule.instrument_class}",
                "状态": rule.status.value,
            }
        )
    removed = registry.remove(name, confirm=True)
    return {"removed": removed.name}

def _parse_decimal(raw: str, field: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_DECIMAL",
            message=f"参数 {field} 的取值 {raw!r} 不是合法数字。",
        ) from exc

@router.get("/market-rules/{name}/check-fees")
def check_fees(name: str, amount: str, direction: str = "buy") -> dict:
    rule = MarketRuleRegistry().get(name)
    engine = RuleEngine(rule)
    amt = _parse_decimal(amount, "amount")
    total = engine.calculate_total_cost(amt, direction)
    return {
        "rule": name,
        "amount": str(amt),
        "direction": direction,
        "items": [
            {"name": item.name, "amount": f"{item.amount:.2f}", "notes": item.notes}
            for item in total.items
        ],
        "total": f"{total.total:.2f}",
    }

@router.get("/market-rules/{name}/check-price-limit")
def check_price_limit(name: str, prev_close: str, condition: str = "") -> dict:
    rule = MarketRuleRegistry().get(name)
    engine = RuleEngine(rule)
    pc = _parse_decimal(prev_close, "prev_close")
    lower, upper = engine.price_limit_range(pc, condition)
    pct = rule.price_limit.default_pct
    if condition:
        for sr in rule.price_limit.special_rules:
            if sr.condition == condition:
                pct = sr.limit_pct
                break
    return {
        "rule": name,
        "prev_close": str(pc),
        "condition": condition,
        "limit_pct": str(pct),
        "upper": f"{upper:.2f}",
        "lower": f"{lower:.2f}",
        "first_day_rule": rule.price_limit.first_day_rule or "",
        "conditions": [sr.condition for sr in rule.price_limit.special_rules],
    }

@router.get("/markets")
def list_markets() -> dict:
    rules = MarketRuleRegistry().list()
    markets = sorted({r.market for r in rules})
    return {"markets": markets}
