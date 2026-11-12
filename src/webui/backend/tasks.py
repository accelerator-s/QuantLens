from __future__ import annotations

import threading
import traceback
import uuid
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from exceptions import ContractViolation, QuantTrainingError
from paths import workspace

_TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS webui_tasks (
  task_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload TEXT NOT NULL
)
"""

class TaskReporter:

    def __init__(self, manager: "TaskManager", record: dict[str, Any]) -> None:
        self._manager = manager
        self._record = record

    def __call__(self, line: str) -> None:
        with self._manager._lock:
            self._record["log"].append(line)
            self._manager._persist(self._record)

    def progress(self, payload: dict[str, Any]) -> None:
        with self._manager._lock:
            self._record["progress"].update(payload)
            self._manager._persist(self._record)

class TaskManager:

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._db_path = workspace().webui_tasks_db
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._load_tasks()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_TASK_SCHEMA)
            conn.commit()

    def _load_tasks(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM webui_tasks ORDER BY created_at DESC LIMIT 2000"
            ).fetchall()
        for (payload,) in reversed(rows):
            record = json.loads(payload)
            if record.get("status") == "running":
                record["status"] = "interrupted"
                record["error"] = "[WEBUI_TASK_INTERRUPTED] WebUI 进程重启，原线程已中断。"
                record["progress"]["state"] = "interrupted"
                record["log"].append("建议：从研究任务详情页核对最后完整 artifact 后启动恢复任务。")
                record["finished_at"] = datetime.now(tz=UTC).isoformat()
                self._pause_research(record)
                self._persist(record)
            self._tasks[record["task_id"]] = record

    @staticmethod
    def _pause_research(record: dict[str, Any]) -> None:
        name = str(record.get("name") or "")
        locked_after_open = False
        if name.startswith("factor-evolution:"):
            research_id = name.partition(":")[2]
            object_id = f"RP-{research_id}"
            try:
                from factor_evolution import EvolutionStore
                from lifecycle.factor_bridge import FactorEvolutionBridge

                events = EvolutionStore().detail(research_id)["events"]
                locked_after_open = any(
                    item["event_type"] == "locked_test_reserved" for item in events
                )
                if locked_after_open:
                    FactorEvolutionBridge().ingest_failure(research_id)
                    record["log"].append(
                        "检测到因子锁定测试已开封，失败运行和曝光已同步统一账本。"
                    )
            except Exception as exc:
                suffix = (
                    "；已确认存在预留事件，按已曝光保守处理。"
                    if locked_after_open
                    else "；未能读取旧事件，统一任务按普通中断暂停并要求人工核查。"
                )
                record["log"].append(
                    f"因子锁定状态同步失败：{type(exc).__name__}: {exc}{suffix}"
                )
        elif name.startswith("strategy-evolution:"):
            object_id = name.partition(":")[2]
        else:
            return
        from exceptions import LifecycleError
        from lifecycle.models import ObjectType
        from lifecycle.states import ActorType
        from lifecycle.store import LifecycleStore

        store = LifecycleStore()
        try:
            state = store.current_state(ObjectType.RESEARCH_PROGRAM, object_id)
        except LifecycleError as exc:
            record["log"].append(
                f"研究恢复状态同步失败：[{exc.code}] {exc.message}"
            )
            return
        if state == "running":
            TaskManager._finish_interrupted_runs(
                store,
                object_id,
                frozen=locked_after_open,
                task_id=record["task_id"],
            )
            if locked_after_open:
                store.transition(
                    ObjectType.RESEARCH_PROGRAM,
                    object_id,
                    "failed",
                    actor_type=ActorType.SYSTEM,
                    actor_ref="webui_task_recovery",
                    reason="因子锁定测试开封后进程中断，禁止恢复或重开测试",
                    payload={
                        "interrupted_task_id": record["task_id"],
                        "failure_code": "LOCKED_TEST_INTERRUPTED",
                    },
                )
                record["log"].append("中断发生在锁定测试开封后，任务已永久终止。")
            else:
                store.transition(
                    ObjectType.RESEARCH_PROGRAM,
                    object_id,
                    "paused",
                    actor_type=ActorType.SYSTEM,
                    actor_ref="webui_task_recovery",
                    reason="WebUI 进程重启，研究已在最后完整 artifact 后暂停",
                    payload={"interrupted_task_id": record["task_id"]},
                )
                record["log"].append(f"研究任务 {object_id} 已转为 paused，可显式恢复。")
        elif state == "frozen":
            TaskManager._finish_interrupted_runs(
                store, object_id, frozen=True, task_id=record["task_id"]
            )
            store.transition(
                ObjectType.RESEARCH_PROGRAM,
                object_id,
                "failed",
                actor_type=ActorType.SYSTEM,
                actor_ref="webui_task_recovery",
                reason="候选冻结或锁定测试开封后进程中断，禁止恢复调参",
                payload={"failure_code": "LOCKED_TEST_INTERRUPTED"},
            )
            record["log"].append("中断发生在候选冻结后，任务已终止且不得续调。")
        elif state == "queued":
            store.transition(
                ObjectType.RESEARCH_PROGRAM,
                object_id,
                "paused",
                actor_type=ActorType.SYSTEM,
                actor_ref="webui_task_recovery",
                reason="WebUI 进程在队列等待阶段重启，任务尚未读取研究数据",
                payload={"interrupted_task_id": record["task_id"]},
            )
            record["log"].append(f"排队任务 {object_id} 已转为 paused，可显式恢复。")

    @staticmethod
    def _finish_interrupted_runs(store, program_id: str, *, frozen: bool, task_id: str) -> None:
        from lifecycle.models import ObjectType
        from lifecycle.states import ActorType

        target = "failed" if frozen else "cancelled"
        for row in store.list_objects(ObjectType.EVALUATION_RUN, limit=5000):
            run = store.get(ObjectType.EVALUATION_RUN, row["object_id"])
            if run.research_program_id != program_id or row["state"] != "running":
                continue
            payload = {"failure_code": "WEBUI_TASK_INTERRUPTED"} if frozen else None
            store.transition(
                ObjectType.EVALUATION_RUN,
                row["object_id"],
                target,
                actor_type=ActorType.SYSTEM,
                actor_ref="webui_task_recovery",
                reason=f"后台任务 {task_id} 因进程重启中断",
                payload=payload,
            )
        if not frozen:
            return
        for row in store.list_objects(ObjectType.LOCKED_TEST_EXPOSURE, limit=5000):
            exposure = store.get(ObjectType.LOCKED_TEST_EXPOSURE, row["object_id"])
            if exposure.research_program_id != program_id or row["state"] != "opened":
                continue
            store.transition(
                ObjectType.LOCKED_TEST_EXPOSURE,
                row["object_id"],
                "failed",
                actor_type=ActorType.SYSTEM,
                actor_ref="webui_task_recovery",
                reason="锁定测试开封后中断，该候选不得恢复调参",
            )

    def _persist(self, record: dict[str, Any]) -> None:
        now = datetime.now(tz=UTC).isoformat()
        payload = json.dumps(record, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO webui_tasks(task_id, created_at, updated_at, payload) "
                "VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "updated_at=excluded.updated_at, payload=excluded.payload",
                (record["task_id"], record["created_at"], now, payload),
            )
            conn.commit()

    def start(
        self,
        name: str,
        fn: Callable[[TaskReporter], Any],
        *,
        resource: str = "",
    ) -> str:
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        record: dict[str, Any] = {
            "task_id": task_id,
            "name": name,
            "status": "running",
            "resource": resource,
            "log": [],
            "error": "",
            "result": None,
            "progress": {
                "state": "queued",
                "percent": 0.0,
                "step": "准备同步",
                "step_index": 0,
                "step_total": 0,
            },
            "created_at": datetime.now(tz=UTC).isoformat(),
            "finished_at": None,
        }
        with self._lock:
            if resource:
                for other in self._tasks.values():
                    if other["status"] == "running" and other.get("resource") == resource:
                        raise ContractViolation(
                            code="WEBUI_TASK_CONFLICT",
                            message=f"资源 {resource} 已有任务 {other['task_id']}（{other['name']}）在运行。",
                            context={"resource": resource, "task_id": other["task_id"]},
                            suggested_action=f"等待任务 {other['task_id']} 结束后重试。",
                        )
            self._tasks[task_id] = record
            self._persist(record)

        reporter = TaskReporter(self, record)

        def runner() -> None:
            try:
                result = fn(reporter)
                with self._lock:
                    record["result"] = result
                    record["status"] = "completed"
                    record["progress"].update({"state": "completed", "percent": 100.0, "fraction": 1.0})
                    self._persist(record)
            except QuantTrainingError as exc:
                with self._lock:
                    record["status"] = "failed"
                    record["error"] = f"[{exc.code}] {exc.message}"
                    record["progress"]["state"] = "failed"
                    if exc.suggested_action:
                        record["log"].append(f"建议：{exc.suggested_action}")
                    self._persist(record)
            except Exception as exc:
                from .errors import unexpected_error
                failure = unexpected_error(exc)
                with self._lock:
                    record["status"] = "failed"
                    record["error"] = f"[{failure.code}] {failure.message}"
                    record["log"].append(f"{failure.context['type']}：{failure.suggested_action}")
                    record["progress"]["state"] = "failed"
                    self._persist(record)
            finally:
                with self._lock:
                    record["finished_at"] = datetime.now(tz=UTC).isoformat()
                    self._persist(record)

        threading.Thread(target=runner, name=f"webui-{task_id}", daemon=True).start()
        return task_id

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                raise QuantTrainingError(
                    code="TASK_NOT_FOUND",
                    message=f"未找到后台任务：{task_id}",
                    context={"task_id": task_id},
                    blocking=True,
                )
            return {**record, "log": list(record["log"]), "progress": dict(record["progress"])}

    def latest(self, *, resource: str, running_only: bool = False) -> dict[str, Any] | None:
        
        with self._lock:
            matches = [
                record
                for record in self._tasks.values()
                if record.get("resource") == resource
                and (not running_only or record["status"] == "running")
            ]
            if not matches:
                return None
            record = max(matches, key=lambda item: item["created_at"])
            return {**record, "log": list(record["log"]), "progress": dict(record["progress"])}

manager = TaskManager()
