from __future__ import annotations

import threading

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from exceptions import QuantTrainingError
from lifecycle.models import ObjectType, ResearchLoop, ResearchProgram
from lifecycle.snapshots import DataSnapshotService
from lifecycle.store import LifecycleStore
from strategy_evolution import (
    StrategyEvolutionEngine,
    StrategyEvolutionConfig,
    build_strategy_auto_config,
    new_program_id,
)
from strategy_evolution.services import StrategyEvolutionServices

from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=["strategy-evolution"])

_MAX_PARALLEL_STRATEGY_RESEARCH = 2
_strategy_research_slots = threading.BoundedSemaphore(
    _MAX_PARALLEL_STRATEGY_RESEARCH
)

def _run_strategy_task(config, program_id: str, append_log) -> dict:
    append_log.progress(
        {"state": "queued", "step": "等待策略研究并发槽位", "percent": 0.0}
    )
    append_log(
        f"已进入受控队列；同时最多运行 {_MAX_PARALLEL_STRATEGY_RESEARCH} 个策略研究。"
    )
    with _strategy_research_slots:
        append_log.progress(
            {"state": "running", "step": "执行多因子策略研究", "percent": 0.01}
        )
        return json_safe(
            StrategyEvolutionEngine().run(
                config, program_id=program_id, on_progress=append_log
            )
        )

class StartPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str
    model_name: str = ""
    trigger_learning_event_id: str = ""

@router.post("/strategy-evolution")
def start_strategy_evolution(payload: StartPayload) -> dict:
    from ai.router import AIRouter

    selected_model = AIRouter().pick(
        "structured_config", model_name=payload.model_name.strip() or None
    ).name
    config = build_strategy_auto_config(payload.profile_name).model_copy(
        update={"model_name": selected_model}
    )
    if payload.trigger_learning_event_id:
        event = LifecycleStore().get(
            ObjectType.LEARNING_EVENT, payload.trigger_learning_event_id
        )
        if event.trigger_research_loop is not ResearchLoop.STRATEGY:
            raise QuantTrainingError(
                code="STRATEGY_EVOLUTION_TRIGGER_INVALID",
                message="LearningEvent 没有触发策略研究的权限声明。",
                blocking=True,
                suggested_action="只使用 verified 且 trigger_research_loop=strategy_research 的事件。",
            )
        config = config.model_copy(
            update={"trigger_learning_event_id": payload.trigger_learning_event_id}
        )
    snapshot = DataSnapshotService().create(require_strict=True)
    program_id = new_program_id(config.profile_name)
    services = StrategyEvolutionServices()
    program = services.begin(config, snapshot, program_id=program_id)

    def run(append_log) -> dict:
        return _run_strategy_task(config, program_id, append_log)

    try:
        task_id = manager.start(
            f"strategy-evolution:{program_id}",
            run,
            resource=f"strategy-evolution:{config.profile_name}",
        )
    except QuantTrainingError:
        services.transition(
            ObjectType.RESEARCH_PROGRAM,
            program_id,
            "cancelled",
            "同一策略已有进化任务运行",
        )
        raise
    return json_safe(
        {
            "task_id": task_id,
            "program_id": program.program_id,
            "data_snapshot_id": snapshot.snapshot_id,
            "auto_config": config,
        }
    )

@router.get("/strategy-evolution")
def list_strategy_evolutions() -> dict:
    store = LifecycleStore()
    items = []
    for row in store.list_objects(ObjectType.RESEARCH_PROGRAM, limit=5000):
        record = store.get(ObjectType.RESEARCH_PROGRAM, row["object_id"])
        assert isinstance(record, ResearchProgram)
        if record.loop is ResearchLoop.STRATEGY:
            items.append({**row, "program": record})
    return json_safe({"items": items})

@router.post("/strategy-evolution/{program_id}/resume")
def resume_strategy_evolution(program_id: str) -> dict:
    store = LifecycleStore()
    program = store.get(ObjectType.RESEARCH_PROGRAM, program_id)
    assert isinstance(program, ResearchProgram)
    state = store.current_state(ObjectType.RESEARCH_PROGRAM, program_id)
    if program.loop is not ResearchLoop.STRATEGY or state != "paused":
        raise QuantTrainingError(
            code="STRATEGY_EVOLUTION_RESUME_NOT_ALLOWED",
            message=f"研究任务 {program_id} 不是可恢复的 paused 策略研究。",
            context={"program_id": program_id, "state": state},
            blocking=True,
            suggested_action="只能在进程中断后从 paused 状态显式恢复。",
        )
    if not program.configuration:
        raise QuantTrainingError(
            code="STRATEGY_EVOLUTION_CONFIG_CHECKPOINT_MISSING",
            message=f"研究任务 {program_id} 没有完整配置检查点。",
            blocking=True,
            suggested_action="保留旧任务审计记录，从 界面 新建研究；不得猜测原切分参数。",
        )
    try:
        config = StrategyEvolutionConfig.model_validate(program.configuration)
    except ValueError as exc:
        raise QuantTrainingError(
            code="STRATEGY_EVOLUTION_CONFIG_CHECKPOINT_INVALID",
            message=f"研究任务 {program_id} 的配置检查点无法校验。",
            context={"validation_error": str(exc)},
            blocking=True,
            suggested_action="停止恢复并核对生命周期账本完整性。",
        ) from exc

    def run(append_log) -> dict:
        return _run_strategy_task(config, program_id, append_log)

    task_id = manager.start(
        f"strategy-evolution:{program_id}",
        run,
        resource=f"strategy-evolution:{config.profile_name}",
    )
    return {"task_id": task_id, "program_id": program_id, "resumed": True}

@router.get("/strategy-evolution/{program_id}")
def get_strategy_evolution(program_id: str) -> dict:
    store = LifecycleStore()
    program = store.get(ObjectType.RESEARCH_PROGRAM, program_id)
    assert isinstance(program, ResearchProgram)
    if program.loop is not ResearchLoop.STRATEGY:
        raise QuantTrainingError(
            code="STRATEGY_EVOLUTION_PROGRAM_TYPE_MISMATCH",
            message=f"研究任务 {program_id} 不是多因子策略循环。",
            blocking=True,
            suggested_action="从策略训练列表选择任务。",
        )
    versions = []
    for row in store.list_objects(ObjectType.STRATEGY_VERSION, limit=5000):
        record = store.get(ObjectType.STRATEGY_VERSION, row["object_id"])
        if record.research_program_id == program_id:
            versions.append({**row, "strategy_version": record})
    runs = []
    for row in store.list_objects(ObjectType.EVALUATION_RUN, limit=5000):
        record = store.get(ObjectType.EVALUATION_RUN, row["object_id"])
        if record.research_program_id == program_id:
            runs.append({**row, "evaluation_run": record})
    return json_safe(
        {
            "program": program,
            "state": store.current_state(ObjectType.RESEARCH_PROGRAM, program_id),
            "events": store.events(ObjectType.RESEARCH_PROGRAM, program_id),
            "strategy_versions": versions,
            "evaluation_runs": runs,
        }
    )

__all__ = ["router"]
