from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from exceptions import LifecycleError, QuantTrainingError
from automation import AutomationConfig, AutomationStore
from lifecycle.champion import ChampionService
from lifecycle.attribution import AttributionService
from lifecycle.daily import DailyOperationsService
from lifecycle.decay import DecayMonitor
from lifecycle.models import LearningEvent, ObjectType, PortfolioPlan
from lifecycle.states import ActorType
from lifecycle.store import LifecycleStore

from ..schemas import confirm_preview
from ..serialize import json_safe

router = APIRouter(tags=["operations"])

class DailyRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(default_factory=list)
    as_of: str = ""

class ObservePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str

class ConfirmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    reason: str = ""
    decided_by: str = ""

def _confirmed_actor(payload: ConfirmPayload) -> str:
    actor = payload.decided_by.strip()
    if not actor:
        raise LifecycleError(
            code="HUMAN_DECISION_ACTOR_REQUIRED",
            message="请填写审批人。",
            blocking=True,
            suggested_action="填写可审计的姓名或内部账号后重新确认。",
        )
    return actor

def _commit_learning_memory(event: LearningEvent, actor: str) -> str:
    from memory_service import MemoryCandidateStore, get_default_adapter

    adapter = get_default_adapter()
    for record in adapter.long_term.iter_records():
        if event.learning_event_id in (record.payload.get("tags") or []):
            return record.candidate_id
    category = (
        "execution_deviation"
        if event.category in {"execution_drift", "user_override", "constraint_drag"}
        else "review_summary"
    )
    candidate, _path = MemoryCandidateStore().create(
        {
            "category": category,
            "summary": event.observation,
            "detail": (
                f"建议：{event.recommended_action}\n"
                f"证据：{', '.join(event.evidence_refs)}"
            ),
            "tags": [event.learning_event_id, event.category, "lifecycle"],
            "related_files": event.evidence_refs,
            "contains_secret": False,
        }
    )
    committed, _path = MemoryCandidateStore().commit(
        candidate.candidate_id, decided_by=actor
    )
    adapter.commit_to_long_term(committed)
    return candidate.candidate_id

class AutomationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    config: AutomationConfig

class RollbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rollback_strategy_version_id: str
    rationale: str = ""

class BrokerFillPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    instrument_id: str
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    executed_at: datetime
    fees: float = Field(default=0, ge=0)

class BrokerExecutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_plan_id: str
    account_id: str
    fills: list[BrokerFillPayload] = Field(min_length=1)
    holding_snapshot_ref: str

@router.post("/operations/daily-run")
def daily_run(payload: DailyRunPayload) -> dict:
    return json_safe(
        DailyOperationsService().run(
            account_ids=payload.account_ids or None,
            as_of=payload.as_of.strip() or None,
        )
    )

@router.get("/operations/automation")
def get_automation() -> dict:
    return json_safe({"config": AutomationStore().load()})

@router.put("/operations/automation")
def put_automation(payload: AutomationPayload) -> dict:
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "保存每日策略调度",
                "启用": "是" if payload.config.enabled else "否",
                "时间": f"{payload.config.daily_time} {payload.config.timezone}",
                "账户": "、".join(payload.config.account_ids) or "全部匹配账户",
                "数据更新": "是" if payload.config.sync_data else "否",
                "说明": "只自动研究、影子运行和生成计划；真实调仓仍需人工确认。",
            }
        )
    AutomationStore().save(payload.config)
    return json_safe({"config": AutomationStore().load()})

@router.post("/operations/shadow-observe")
def observe_shadows(payload: ObservePayload) -> dict:
    return json_safe(
        DailyOperationsService().observe_matured_shadows(as_of=payload.as_of)
    )

@router.post("/operations/attribution-mature")
def mature_attribution(payload: ObservePayload) -> dict:
    return json_safe(AttributionService().mature(as_of=payload.as_of))

@router.post("/operations/broker-executions")
def receive_broker_executions(payload: BrokerExecutionPayload) -> dict:
    from lifecycle.execution import ExecutionFeedbackService

    record = ExecutionFeedbackService().record_broker(
        plan_id=payload.portfolio_plan_id,
        account_id=payload.account_id,
        fills=[item.model_dump(mode="python") for item in payload.fills],
        holding_snapshot_ref=payload.holding_snapshot_ref,
    )
    return json_safe({"execution_record": record, "state": "reconciled"})

@router.post("/operations/execution-recovery")
def recover_manual_executions(payload: DailyRunPayload) -> dict:
    from lifecycle.execution import ExecutionFeedbackService

    return json_safe(
        ExecutionFeedbackService().recover_manual_trades(
            account_ids=payload.account_ids or None
        )
    )

@router.get("/operations/attributions")
def list_attributions(
    account_id: str = "", limit: int = Query(default=200, ge=1, le=5000)
) -> dict:
    store = LifecycleStore()
    items = []
    for row in store.list_objects(ObjectType.ATTRIBUTION_RECORD, limit=limit):
        record = store.get(ObjectType.ATTRIBUTION_RECORD, row["object_id"])
        plan = store.get(ObjectType.PORTFOLIO_PLAN, record.portfolio_plan_id)
        if account_id and account_id not in plan.account_ids:
            continue
        items.append({**row, "attribution": record})
    return json_safe(
        {"items": items}
    )

@router.post("/operations/decay-scan")
def decay_scan() -> dict:
    scan = DecayMonitor().scan()
    triggered = []
    trigger_errors = []
    store = LifecycleStore()
    for event in scan["trigger_candidates"]:
        if event.trigger_research_loop is None:
            continue
        source = event.source_object_refs[-1]
        attribution = store.get(source.object_type, source.object_id)
        strategy = store.get(
            ObjectType.STRATEGY_VERSION, attribution.strategy_version_id
        )
        try:
            from .strategy_evolution import StartPayload, start_strategy_evolution

            started = start_strategy_evolution(
                StartPayload(
                    profile_name=strategy.strategy_name,
                    trigger_learning_event_id=event.learning_event_id,
                )
            )
            triggered.append(
                {
                    "learning_event_id": event.learning_event_id,
                    "program_id": started["program_id"],
                    "task_id": started["task_id"],
                }
            )
        except QuantTrainingError as exc:
            trigger_errors.append(
                {
                    "learning_event_id": event.learning_event_id,
                    "code": exc.code,
                    "message": exc.message,
                    "suggested_action": exc.suggested_action,
                }
            )
    return json_safe(
        {**scan, "triggered_research": triggered, "trigger_errors": trigger_errors}
    )

@router.get("/operations/learning-events")
def list_learning_events(limit: int = Query(default=200, ge=1, le=5000)) -> dict:
    store = LifecycleStore()
    return json_safe(
        {
            "items": [
                {
                    **row,
                    "learning_event": store.get(
                        ObjectType.LEARNING_EVENT, row["object_id"]
                    ),
                }
                for row in store.list_objects(ObjectType.LEARNING_EVENT, limit=limit)
            ]
        }
    )

@router.post("/operations/learning-events/{event_id}/accept")
def accept_learning_event(event_id: str, payload: ConfirmPayload) -> dict:
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "接受经验事实为长期研究输入",
                "事件": event_id,
                "说明": "AI 下一轮研究将读取该事实，但不会因此自动晋升正式策略。",
            }
        )
    actor = _confirmed_actor(payload)
    store = LifecycleStore()
    event = store.get(ObjectType.LEARNING_EVENT, event_id)
    assert isinstance(event, LearningEvent)
    memory_candidate_id = _commit_learning_memory(event, actor)
    if store.current_state(ObjectType.LEARNING_EVENT, event_id) != "accepted":
        store.transition(
            ObjectType.LEARNING_EVENT,
            event_id,
            "accepted",
            actor_type=ActorType.HUMAN,
            actor_ref=actor,
            reason=payload.reason.strip() or "用户审阅归因证据后接受为长期经验",
            payload={"memory_candidate_id": memory_candidate_id},
        )
    return {
        "learning_event_id": event_id,
        "state": "accepted",
        "memory_candidate_id": memory_candidate_id,
    }

@router.get("/operations/plans")
def list_canonical_plans(
    account_id: str = "", limit: int = Query(default=200, ge=1, le=5000)
) -> dict:
    store = LifecycleStore()
    items = []
    for row in store.list_objects(ObjectType.PORTFOLIO_PLAN, limit=limit):
        plan = store.get(ObjectType.PORTFOLIO_PLAN, row["object_id"])
        assert isinstance(plan, PortfolioPlan)
        if account_id and account_id not in plan.account_ids:
            continue
        items.append({**row, "portfolio_plan": plan})
    return json_safe({"items": items})

@router.post("/operations/plans/{plan_id}/confirm")
def confirm_plan(plan_id: str, payload: ConfirmPayload) -> dict:
    store = LifecycleStore()
    plan = store.get(ObjectType.PORTFOLIO_PLAN, plan_id)
    assert isinstance(plan, PortfolioPlan)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "确认真实调仓计划",
                "计划": plan_id,
                "策略版本": plan.strategy_version_id,
                "账户": "、".join(plan.account_ids),
                "建议交易数": str(len(plan.proposed_orders)),
                "说明": "只有 champion 计划可确认；确认不代表已成交。",
            }
        )
    strategy_state = store.current_state(
        ObjectType.STRATEGY_VERSION, plan.strategy_version_id
    )
    if strategy_state != "champion":
        raise LifecycleError(
            code="PORTFOLIO_PLAN_CHAMPION_REQUIRED",
            message=(
                f"计划 {plan_id} 来自 {strategy_state} 策略，"
                "只能作为影子目标，不得确认为真实调仓。"
            ),
            blocking=True,
            suggested_action="仅确认当前 champion 的计划；challenger/watching 继续影子观察。",
        )
    try:
        execution_end = datetime.fromisoformat(plan.execution_window.rsplit("..", 1)[1])
    except (IndexError, ValueError) as exc:
        raise LifecycleError(
            code="PORTFOLIO_PLAN_EXECUTION_WINDOW_INVALID",
            message=f"计划 {plan_id} 的执行窗口无法解析。",
            blocking=True,
            suggested_action="拒绝该计划并由 champion 重新生成。",
        ) from exc
    if execution_end.tzinfo is None:
        execution_end = execution_end.replace(tzinfo=UTC)
    if execution_end.timetz().replace(tzinfo=None) == time.min:
        execution_end = datetime.combine(
            execution_end.date(), time.max, tzinfo=execution_end.tzinfo
        )
    if datetime.now(tz=UTC) > execution_end.astimezone(UTC):
        if store.current_state(ObjectType.PORTFOLIO_PLAN, plan_id) == "proposed":
            store.transition(
                ObjectType.PORTFOLIO_PLAN,
                plan_id,
                "expired",
                actor_type=ActorType.SYSTEM,
                actor_ref="daily_operations",
                reason="人工确认时执行窗口已经结束",
            )
        raise LifecycleError(
            code="PORTFOLIO_PLAN_EXECUTION_WINDOW_EXPIRED",
            message=f"计划 {plan_id} 的执行窗口已经结束，禁止确认。",
            blocking=True,
            suggested_action="重新运行每日闭环生成当前交易日的新计划。",
        )
    current = store.current_state(ObjectType.PORTFOLIO_PLAN, plan_id)
    if current == "active":
        return json_safe({"plan_id": plan_id, "state": "active"})
    if current not in {"proposed", "confirmed"}:
        raise LifecycleError(
            code="PORTFOLIO_PLAN_NOT_PROPOSED",
            message=f"计划 {plan_id} 当前为 {current}，不能确认。",
            blocking=True,
            suggested_action="刷新计划状态后选择 proposed/confirmed 的 champion 计划。",
        )
    actor = _confirmed_actor(payload)
    if current == "proposed":
        store.transition(
            ObjectType.PORTFOLIO_PLAN,
            plan_id,
            "confirmed",
            actor_type=ActorType.HUMAN,
            actor_ref=actor,
            reason=payload.reason.strip() or "用户审阅后确认正式调仓计划",
        )
    store.transition(
        ObjectType.PORTFOLIO_PLAN,
        plan_id,
        "active",
        actor_type=ActorType.SYSTEM,
        actor_ref="daily_operations",
        reason="已确认计划进入可执行窗口",
    )
    return json_safe({"plan_id": plan_id, "state": "active"})

@router.post("/operations/plans/{plan_id}/reject")
def reject_plan(plan_id: str, payload: ConfirmPayload) -> dict:
    if not payload.confirm:
        return confirm_preview({"操作": "拒绝调仓计划", "计划": plan_id})
    actor = _confirmed_actor(payload)
    store = LifecycleStore()
    current = store.current_state(ObjectType.PORTFOLIO_PLAN, plan_id)
    if current == "rejected":
        return {"plan_id": plan_id, "state": "rejected"}
    if current != "proposed":
        raise LifecycleError(
            code="PORTFOLIO_PLAN_NOT_PROPOSED",
            message=f"计划 {plan_id} 当前不是 proposed，不能拒绝。",
            blocking=True,
            suggested_action="刷新计划状态后选择合法操作。",
        )
    store.transition(
        ObjectType.PORTFOLIO_PLAN,
        plan_id,
        "rejected",
        actor_type=ActorType.HUMAN,
        actor_ref=actor,
        reason=payload.reason.strip() or "用户拒绝本次调仓建议",
    )
    return {"plan_id": plan_id, "state": "rejected"}

@router.get("/operations/champion-slots")
def champion_slots() -> dict:
    from lifecycle.governance import load_champion_policy

    return json_safe(
        {
            "items": ChampionService().slots(),
            "policy": load_champion_policy(),
        }
    )

@router.post("/operations/strategies/{strategy_id}/bootstrap-champion")
def bootstrap_champion(strategy_id: str, payload: ConfirmPayload) -> dict:
    if not payload.confirm:
        from lifecycle.governance import load_champion_policy

        required = load_champion_policy().min_shadow_observations
        return confirm_preview(
            {
                "操作": "建立首个 champion",
                "策略版本": strategy_id,
                "说明": f"必须已完成唯一锁定测试和至少 {required} 个到期影子观察。",
            }
        )
    actor = _confirmed_actor(payload)
    ChampionService().bootstrap(
        strategy_id,
        reason=payload.reason.strip() or "用户核验锁定测试与影子证据后建立首个 champion",
        decided_by=actor,
    )
    return {"strategy_version_id": strategy_id, "state": "champion"}

@router.post("/operations/strategies/{strategy_id}/propose-promotion")
def propose_promotion(strategy_id: str) -> dict:
    return json_safe({"decision": ChampionService().propose(strategy_id)})

@router.post("/operations/strategies/{strategy_id}/propose-rollback")
def propose_rollback(strategy_id: str, payload: RollbackPayload) -> dict:
    decision = ChampionService().propose_rollback(
        strategy_id,
        payload.rollback_strategy_version_id,
        rationale=payload.rationale.strip() or "实盘归因或风险证据触发人工回滚审阅",
    )
    return json_safe({"decision": decision})

@router.post("/operations/champion-decisions/{decision_id}/approve")
def approve_promotion(decision_id: str, payload: ConfirmPayload) -> dict:
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "批准 champion/challenger 切换",
                "决策": decision_id,
                "说明": "批准后新版本成为 champion，旧版本保留为 watching 回滚目标。",
            }
        )
    actor = _confirmed_actor(payload)
    ChampionService().approve(
        decision_id,
        reason=payload.reason.strip() or "用户核验同口径影子证据后批准晋升",
        decided_by=actor,
    )
    return {"decision_id": decision_id, "state": "applied"}

__all__ = ["router"]
