from __future__ import annotations

from decimal import Decimal, InvalidOperation

import yaml
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from exceptions import ContractViolation
from market_rules import RuleEngine
from market_rules.models import MarketRuleStatus
from market_rules.registry import MarketRuleRegistry
from presentation import factor_display_name, strategy_display_name
from strategy.models import StrategyProfileDoc, StrategyStatus, validate_transition
from strategy.store import StrategyProfileStore

from ..schemas import ConfirmPayload, DecidePayload, TransitionPayload, confirm_preview, parse_enum
from ..serialize import json_safe

router = APIRouter(tags=["strategy"])

def _profile_summary(doc) -> dict:
    return json_safe(
        {
            "profile_name": doc.profile_name,
            "display_name": doc.display_name or strategy_display_name(doc.profile_name),
            "status": doc.status,
            "is_template": doc.is_template,
            "inheritance_parent": doc.inheritance_parent,
            "market": doc.market,
            "instruments": doc.instruments,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }
    )

@router.get("/strategies")
def list_strategies(
    status: str = "", templates: str = "", include_internal: bool = False
) -> dict:
    store = StrategyProfileStore()
    items = store.list(
        status=parse_enum(StrategyStatus, status, "status") if status else None,
        templates_only={"true": True, "false": False}.get(templates.lower()) if templates else None,
    )
    if not include_internal:
        items = [doc for doc in items if not doc.extra.get("internal_only")]
    return {
        "items": [_profile_summary(doc) for doc in items],
        "statuses": [s.value for s in StrategyStatus],
    }

@router.get("/strategies/{name}")
def get_strategy(name: str) -> dict:
    store = StrategyProfileStore()
    doc = store.get(name)
    resolved = None
    resolve_error = ""
    try:
        resolved = store.resolve(name)
    except Exception as exc:  
        resolve_error = str(exc)
    factor_names = {
        item.name: factor_display_name(item.name)
        for item in (resolved.factors.items if resolved else [])
    }
    return json_safe(
        {
            "profile": doc,
            "resolved": resolved,
            "resolve_error": resolve_error,
            "display_name": doc.display_name or strategy_display_name(doc.profile_name),
            "inheritance_display_name": strategy_display_name(doc.inheritance_parent),
            "factor_display_names": factor_names,
        }
    )

class StrategyUpsertPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml_text: str
    override: bool = False
    confirm: bool = False

@router.post("/strategies")
def upsert_strategy(payload: StrategyUpsertPayload) -> dict:
    raw = yaml.safe_load(payload.yaml_text) or {}
    doc = StrategyProfileDoc.model_validate(raw)
    store = StrategyProfileStore()
    if not payload.confirm:
        exists = store._profile_path(doc.profile_name).exists()
        return confirm_preview(
            {
                "操作": "覆盖已有策略配置" if exists else "新建策略配置",
                "策略": doc.profile_name,
                "模板": "是" if doc.is_template else "否",
                "继承自": doc.inheritance_parent or "（无）",
                "市场": doc.market,
                "覆盖模式": "是" if payload.override else "否",
            }
        )
    store.upsert(doc, override=payload.override)
    return json_safe({"profile": store.get(doc.profile_name)})

@router.post("/strategies/{name}/transition")
def transition_strategy(name: str, payload: TransitionPayload) -> dict:
    store = StrategyProfileStore()
    target = parse_enum(StrategyStatus, payload.target, "target")
    if not payload.confirm:
        doc = store.get(name)
        validate_transition(doc.status, target)
        preview = {
            "操作": f"策略状态流转 -> {target.value}",
            "策略": doc.profile_name,
            "当前状态": doc.status.value,
            "目标状态": target.value,
        }
        if target == StrategyStatus.ACTIVE:
            warnings = store.validate_for_activation(name)
            preview["激活校验"] = "；".join(warnings) if warnings else "通过（无警告）"
        return confirm_preview(preview)
    doc = store.transition(name, target=target, confirm=True)
    return json_safe({"profile": doc})

@router.post("/strategies/{name}/delete")
def delete_strategy(name: str, payload: DecidePayload) -> dict:
    store = StrategyProfileStore()
    if not payload.confirm:
        doc = store.get(name)
        return confirm_preview(
            {
                "操作": "删除策略配置（移入回收站）",
                "策略": doc.profile_name,
                "状态": doc.status.value,
                "说明": "active 状态与仍被继承的配置会被后端拒绝删除。",
            }
        )
    bucket = store.delete(
        name,
        confirm=True,
        reason=payload.note,
        deleted_by=payload.decided_by or "webui",
    )
    return {"trash_bucket": str(bucket)}

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
