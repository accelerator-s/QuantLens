from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from exceptions import ContractViolation
from strategy import StrategyProfileStore, StrategyStatus
from strategy.research import (MODELS, ResearchSpec, create_strategy, defaults, is_research,
                               read_result, require_strategy, result_path, set_status, start_research)
from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=["strategy"])


def library_task(name):
    task = manager.latest(resource=f"strategy-research:{name}")
    if not task or task['status'] == 'completed':
        return None
    return {key: task[key] for key in ["task_id", "status", "progress", "error"]}


def summary(doc):
    spec = doc.extra["research_spec"]
    task = library_task(doc.profile_name)
    return json_safe({"profile_name": doc.profile_name, "display_name": doc.display_name,
        "status": doc.status, "model": spec["model"], "factors": spec["factors"],
        "start": spec["start"], "end": spec["end"], "updated_at": doc.updated_at,
        "account_id": spec['account_id'], 'account_snapshot': doc.extra.get('research_account_snapshot') or doc.extra['creation_account_snapshot'],
        'capital_suspension': doc.extra.get('capital_suspension'),
        "task": task})


@router.get("/strategies")
def list_strategies(status: StrategyStatus | None = None):
    return {"items": [summary(doc) for doc in StrategyProfileStore().list(status=status) if is_research(doc)],
            "statuses": [s.value for s in StrategyStatus]}


@router.get("/strategies/options")
def research_options():
    from factors.registry import FactorRegistry, FactorStatus
    return json_safe({"models": MODELS, "defaults": defaults(), "factors": [
        {"name": f.name, "display_name": f.display_name or f.name} for f in FactorRegistry().list()
        if f.status in {FactorStatus.ACTIVE, FactorStatus.DRAFT} and f.direction in {"positive", "negative"}
    ]})


@router.post("/strategies")
def new_strategy(payload: ResearchSpec):
    doc = create_strategy(payload)
    return start_research(doc.profile_name)


@router.get("/strategies/{name}")
def strategy_detail(name: str):
    from presentation import factor_display_name
    doc = require_strategy(name)
    result = read_result(name)
    task = library_task(name)
    # A completed compute export is not a published result until sync succeeds.
    if result and result.get("state") in {"running", "computed"} and task and task["status"] in {"failed", "interrupted"}:
        result = {**result, "state": "failed", "error": task.get("error") or "研究任务已中断，请重新运行。"}
    return json_safe({**summary(doc), "spec": doc.extra["research_spec"], "result": result,
                      "hypothesis": doc.extra.get("research_hypothesis", ""),
                      "task": task, "factor_display_names": {f.name: factor_display_name(f.name) for f in doc.factors.items}})


@router.post("/strategies/{name}/research")
def research_strategy(name: str):
    return start_research(name)


class StatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: StrategyStatus


@router.put("/strategies/{name}/status")
def strategy_status(name: str, payload: StatusPayload):
    return {"profile": json_safe(set_status(name, payload.status))}


@router.delete("/strategies/{name}")
def delete_strategy(name: str):
    require_strategy(name)
    if manager.latest(resource=f"strategy-research:{name}", running_only=True):
        raise ContractViolation(code="STRATEGY_BUSY", message="研究任务运行中，请等待完成后删除。")
    store = StrategyProfileStore()
    store._profile_path(name).unlink()
    result_path(name).unlink(missing_ok=True)
    store._rebuild_meta()
    return {"deleted": name}
