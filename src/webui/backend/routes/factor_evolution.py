from __future__ import annotations

import threading
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from exceptions import QuantTrainingError
from factor_evolution import (
    EvolutionEngine,
    EvolutionStore,
    LockedTestLedger,
    ProductionEvolutionServices,
    build_auto_config,
)
from factor_evolution.models import EvolutionDecision, EvolutionState
from lifecycle.factor_bridge import FactorEvolutionBridge
from lifecycle.snapshots import DataSnapshotService
from presentation import ARCHIVED_FACTORS, factor_display_name, strategy_display_name

from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=["factor-evolution"])

_MAX_PARALLEL_FACTOR_RESEARCH = 3
_factor_research_slots = threading.BoundedSemaphore(_MAX_PARALLEL_FACTOR_RESEARCH)

class AutoEvolutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_factor: str = Field(min_length=1)

def _resolve_evolution_model(model_name: str) -> str:
    
    from ai.router import AIRouter

    ai_router = AIRouter()
    requested = model_name.strip()
    if requested:
        ai_router.pick("factor_candidate", model_name=requested)
        ai_router.pick("structured_config", model_name=requested)
        return requested
    
    for decision in ai_router.candidates_for("factor_candidate"):
        try:
            ai_router.pick("structured_config", model_name=decision.model.name)
            return decision.model.name
        except QuantTrainingError:
            continue
    raise QuantTrainingError(
        code="EVOLUTION_AI_MODEL_UNAVAILABLE",
        message="没有同时具备因子生成和结果复盘权限的可用 AI。",
        blocking=True,
        suggested_action="在 模型连接页补充能力标签与 接口密钥后重试。",
    )

def _start_task(research_id: str, base_factor: str) -> str:
    def run(append_log) -> dict:
        bridge = FactorEvolutionBridge()
        bridge.mark_running(research_id)
        append_log.progress(
            {"state": "queued", "step": "等待因子研究并发槽位", "percent": 0.0}
        )
        append_log(
            f"已进入受控研究队列；同时最多运行 {_MAX_PARALLEL_FACTOR_RESEARCH} 个因子任务。"
        )
        with _factor_research_slots:
            append_log.progress(
                {"state": "running", "step": "执行因子研究循环", "percent": 0.01}
            )
            engine = EvolutionEngine(EvolutionStore(), ProductionEvolutionServices(on_progress=append_log))
            try:
                detail = engine.run(research_id, on_progress=append_log)
                lifecycle = bridge.ingest(research_id)
                if update := lifecycle.get('catalog_update'):
                    append_log(f"因子已更新：{update['factor_name']} {update['from_version']} → {update['to_version']}")
                append_log(f"统一生命周期对象登记完成：{lifecycle['program_id']}")
                return json_safe({"evolution": detail, "lifecycle": lifecycle})
            except QuantTrainingError as exc:
                try:
                    synced = bridge.ingest_failure(research_id)
                    append_log(
                        f"失败事实已同步统一账本：{synced['program_id']}"
                    )
                except Exception as sync_exc:
                    bridge.mark_failed(
                        research_id,
                        code="FACTOR_EVOLUTION_FAILURE_SYNC_FAILED",
                        message=f"原错误 [{exc.code}]；失败事实同步又失败：{sync_exc}",
                    )
                    raise QuantTrainingError(
                        code="FACTOR_EVOLUTION_FAILURE_SYNC_FAILED",
                        message="因子研究失败，且统一失败事实同步未完成。",
                        context={
                            "original_code": exc.code,
                            "sync_exception": type(sync_exc).__name__,
                        },
                        blocking=True,
                        suggested_action="核对旧研究事件和统一账本；锁定测试若已预留，一律视为已曝光。",
                    ) from sync_exc
                bridge.mark_failed(research_id, code=exc.code, message=exc.message)
                raise
            except Exception as exc:
                wrapped = QuantTrainingError(
                    code="FACTOR_EVOLUTION_LIFECYCLE_FAILED",
                    message=f"因子研究闭环登记失败：{exc}",
                    context={"research_id": research_id, "exception": type(exc).__name__},
                    blocking=True,
                    suggested_action="检查研究记录、回测结果与关联资料后继续任务。",
                )
                try:
                    bridge.ingest_failure(research_id)
                except Exception as sync_exc:
                    wrapped = QuantTrainingError(
                        code="FACTOR_EVOLUTION_FAILURE_SYNC_FAILED",
                        message=f"因子研究异常，且失败事实同步失败：{sync_exc}",
                        context={
                            "original_exception": type(exc).__name__,
                            "sync_exception": type(sync_exc).__name__,
                        },
                        blocking=True,
                        suggested_action="核对旧研究事件和统一账本；锁定测试若已预留，一律视为已曝光。",
                    )
                bridge.mark_failed(research_id, code=wrapped.code, message=wrapped.message)
                raise wrapped from exc

    return manager.start(
        f"factor-evolution:{research_id}",
        run,
        resource=f"factor-evolution:{base_factor}",
    )

@router.post("/factor-evolution")
def start_evolution(payload: AutoEvolutionPayload) -> dict:
    return start_factor_evolution(payload.base_factor)

def start_factor_evolution(base_factor: str, *, model_name: str = "") -> dict:
    
    selected_model = _resolve_evolution_model(model_name)
    config = build_auto_config(base_factor).model_copy(update={"model_name": selected_model})
    snapshot = DataSnapshotService().create(require_strict=True)
    store = EvolutionStore()
    engine = EvolutionEngine(store, ProductionEvolutionServices())
    research_id = engine.create(config)
    program = FactorEvolutionBridge().begin(research_id, config, snapshot)
    try:
        task_id = _start_task(research_id, config.base_factor)
    except QuantTrainingError as exc:
        status = store.load_status(research_id)
        status.state = EvolutionState.FAILED
        status.decision = EvolutionDecision.FAILED
        status.error = f"[{exc.code}] {exc.message}"
        status.terminal_reason = "同一因子已有自动进化任务"
        store.save_status(status)
        store.append_event(
            research_id, "session_failed", {"code": exc.code, "message": exc.message}
        )
        FactorEvolutionBridge().mark_failed(
            research_id, code=exc.code, message=exc.message
        )
        raise
    return {
        "research_id": research_id,
        "task_id": task_id,
        "program_id": program.program_id,
        "data_snapshot_id": snapshot.snapshot_id,
        "auto_config": json_safe(config),
    }

@router.get("/factor-evolution")
def list_evolution(include_archived: bool = False) -> dict:
    items = EvolutionStore().list()
    if not include_archived:
        items = [item for item in items if item["base_factor"] not in ARCHIVED_FACTORS]
    for item in items:
        item["base_factor_display_name"] = factor_display_name(item["base_factor"])
        item["profile_display_name"] = strategy_display_name(item["profile_name"])
        task = manager.latest(name=f"factor-evolution:{item['research_id']}")
        if task:
            item["task_id"] = task["task_id"]
            if task["status"] == "interrupted":
                item["state"] = "paused"
            elif task["status"] == "failed" and item["state"] != "cancelled":
                item["state"] = "failed"
            elif task["progress"].get("state") == "queued":
                item["state"] = "queued"
    return json_safe({"items": items})

@router.get("/factor-evolution/locked-tests")
def list_locked_tests(base_factor: str = "") -> dict:
    return json_safe({"items": LockedTestLedger().exposures(base_factor=base_factor)})

@router.post("/factor-evolution/{research_id}/resume")
def resume_evolution(research_id: str) -> dict:
    from lifecycle.models import ObjectType
    from lifecycle.store import LifecycleStore

    bridge = FactorEvolutionBridge()
    program_id = bridge.program_id(research_id)
    state = LifecycleStore().current_state(ObjectType.RESEARCH_PROGRAM, program_id)
    if state != "paused":
        raise QuantTrainingError(
            code="FACTOR_EVOLUTION_NOT_PAUSED",
            message=f"因子研究 {research_id} 当前为 {state}，不能恢复。",
            blocking=True,
            suggested_action="只能继续因进程中断而暂停、且未进入锁定测试的任务。",
        )
    detail = EvolutionStore().detail(research_id)
    detail["base_factor_display_name"] = factor_display_name(
        detail["config"]["base_factor"]
    )
    detail["profile_display_name"] = strategy_display_name(
        detail["config"]["profile_name"]
    )
    task_id = _start_task(research_id, detail["config"]["base_factor"])
    return {"research_id": research_id, "program_id": program_id, "task_id": task_id}

@router.get("/factor-evolution/{research_id}")
def get_evolution(research_id: str) -> dict:
    from exceptions import LifecycleError
    from lifecycle.models import ObjectType
    from lifecycle.store import LifecycleStore

    detail = EvolutionStore().detail(research_id)
    task = manager.latest(name=f"factor-evolution:{research_id}")
    detail["task"] = ({key: task.get(key) for key in
                       ("task_id", "status", "progress", "log", "error")} if task else None)
    detail["experiment_ids"] = _session_experiment_ids(research_id, detail)
    status = detail["status"]
    if status.get("started_at") and status.get("state") == "running":
        endpoint = status.get("finished_at") or (task or {}).get("finished_at")
        now = datetime.fromisoformat(endpoint) if endpoint else datetime.now(tz=UTC)
        status["elapsed_seconds"] = max(status.get("elapsed_seconds", 0),
                                         (now - datetime.fromisoformat(status["started_at"])).total_seconds())
    config_model = str((detail.get("config") or {}).get("model_name") or "")
    candidate_model = next(
        (
            str(item.get("model_name") or "")
            for item in reversed(detail.get("versions") or [])
            if item.get("model_name")
        ),
        "",
    )
    review_model = next(
        (
            str(
                (item.get("payload") or {}).get("model_name")
                or ((item.get("payload") or {}).get("review") or {}).get("model_name")
                or ""
            )
            for item in reversed(detail.get("events") or [])
            if item.get("event_type") in {"round_completed", "review_generation_failed"}
            and (
                (item.get("payload") or {}).get("model_name")
                or ((item.get("payload") or {}).get("review") or {}).get("model_name")
            )
        ),
        "",
    )
    routed: dict[str, str] = {}
    routing_error = ""
    if not candidate_model or not review_model:
        from ai.router import AIRouter

        try:
            ai_router = AIRouter()
            routed = {
                "candidate": ai_router.pick(
                    "factor_candidate", model_name=config_model or None
                ).name,
                "review": ai_router.pick(
                    "structured_config", model_name=config_model or None
                ).name,
            }
        except QuantTrainingError as exc:
            routing_error = f"[{exc.code}] {exc.message}"
    detail["ai_models"] = {
        "candidate": candidate_model or routed.get("candidate", ""),
        "candidate_source": "recorded" if candidate_model else "current_route",
        "review": review_model or routed.get("review", ""),
        "review_source": "recorded" if review_model else "current_route",
        "routing_error": routing_error,
    }
    detail["locked_test_exposures"] = [
        row
        for row in LockedTestLedger().exposures(base_factor=detail["config"]["base_factor"])
        if row["research_id"] == research_id
    ]
    try:
        lifecycle_store = LifecycleStore()
        program_id = FactorEvolutionBridge().program_id(research_id)
        detail["lifecycle_state"] = lifecycle_store.current_state(
            ObjectType.RESEARCH_PROGRAM,
            program_id,
        )
        detail["factor_versions"] = [
            {
                **row,
                "factor_version": lifecycle_store.get(
                    ObjectType.FACTOR_VERSION, row["object_id"]
                ),
            }
            for row in lifecycle_store.list_objects(
                ObjectType.FACTOR_VERSION, limit=5000
            )
            if lifecycle_store.get(
                ObjectType.FACTOR_VERSION, row["object_id"]
            ).research_program_id
            == program_id
        ]
        detail["research_report"] = next(
            (
                {
                    **row,
                    "research_report": lifecycle_store.get(
                        ObjectType.RESEARCH_REPORT, row["object_id"]
                    ),
                }
                for row in lifecycle_store.list_objects(
                    ObjectType.RESEARCH_REPORT, limit=5000
                )
                if lifecycle_store.get(
                    ObjectType.RESEARCH_REPORT, row["object_id"]
                ).research_program_id
                == program_id
            ),
            None,
        )
    except LifecycleError as exc:
        detail["lifecycle_state"] = "legacy_unmanaged"
        detail["lifecycle_error"] = {
            "code": exc.code,
            "message": exc.message,
        }
    return json_safe(detail)


def _session_experiment_ids(research_id: str, detail: dict) -> list[str]:
    """Include registered baselines before the first completed evaluation."""
    events = detail.get("events") or []
    ids = {event["payload"]["experiment_id"] for event in events
           if (event.get("payload") or {}).get("experiment_id")}
    for event in events:
        nested = (event.get("payload") or {}).get("result") or {}
        if nested.get("experiment_id"):
            ids.add(nested["experiment_id"])
    if any(event["event_type"] == "baseline_started" for event in events):
        return sorted(ids)
    # Older sessions did not store the baseline ID until evaluation finished.
    # Recover only an unambiguous root experiment inside this factor's session.
    from experiments.store import ExperimentStore

    config = detail["config"]
    start = next((event["created_at"] for event in events if event["event_type"] == "session_created"),
                 detail["status"].get("started_at"))
    if not start:
        return sorted(ids)
    boundaries = [row["started_at"] for row in EvolutionStore().list()
                  if row["research_id"] != research_id and row["base_factor"] == config["base_factor"]
                  and row.get("started_at") and row["started_at"] > start]
    if detail["status"].get("finished_at"):
        boundaries.append(detail["status"]["finished_at"])
    end = min(boundaries) if boundaries else None
    rows = ExperimentStore().list_experiments(subject_type="factor", subject_name=config["base_factor"])
    roots = [row for row in rows if not row.get("parent_exp_id")
             and row.get("profile_name") == config["profile_name"]
             and row["created_at"] >= start and (end is None or row["created_at"] < end)]
    if len(roots) == 1:
        ids.add(roots[0]["exp_id"])
    return sorted(ids)
