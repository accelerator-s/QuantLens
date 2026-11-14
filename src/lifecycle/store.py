from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from exceptions import LifecycleError
from paths import workspace

from .models import (
    ChampionDecision,
    DataSnapshot,
    FactorEvaluation,
    LockedTestExposure,
    ObjectType,
    QualityStatus,
    Record,
    RunPurpose,
    StrategyChangeDimension,
    StrategyVersion,
    object_id_of,
    object_type_of,
    parse_record,
    record_references,
)
from .states import ActorType, INITIAL_STATES, validate_transition

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lifecycle_objects (
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  PRIMARY KEY (object_type, object_id)
);

CREATE TABLE IF NOT EXISTS lifecycle_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  from_state TEXT NOT NULL,
  to_state TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_ref TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL,
  FOREIGN KEY (object_type, object_id)
    REFERENCES lifecycle_objects(object_type, object_id)
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_events_object
  ON lifecycle_events(object_type, object_id, event_id);
"""

_TRIGGERS: dict[str, str] = {
    "trg_lifecycle_object_no_update": (
        "CREATE TRIGGER trg_lifecycle_object_no_update BEFORE UPDATE ON lifecycle_objects "
        "BEGIN SELECT RAISE(ABORT, 'LIFECYCLE_APPEND_ONLY'); END"
    ),
    "trg_lifecycle_object_no_delete": (
        "CREATE TRIGGER trg_lifecycle_object_no_delete BEFORE DELETE ON lifecycle_objects "
        "BEGIN SELECT RAISE(ABORT, 'LIFECYCLE_APPEND_ONLY'); END"
    ),
    "trg_lifecycle_event_no_update": (
        "CREATE TRIGGER trg_lifecycle_event_no_update BEFORE UPDATE ON lifecycle_events "
        "BEGIN SELECT RAISE(ABORT, 'LIFECYCLE_APPEND_ONLY'); END"
    ),
    "trg_lifecycle_event_no_delete": (
        "CREATE TRIGGER trg_lifecycle_event_no_delete BEFORE DELETE ON lifecycle_events "
        "BEGIN SELECT RAISE(ABORT, 'LIFECYCLE_APPEND_ONLY'); END"
    ),
}

def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()

def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def _digest(payload_text: str) -> str:
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

class LifecycleStore:

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or workspace().lifecycle_db
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        existed = self.db_path.exists()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            if existed:
                self._verify_triggers(conn)
            else:
                for ddl in _TRIGGERS.values():
                    conn.execute(ddl)
            conn.commit()

    @staticmethod
    def _verify_triggers(conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        present = {str(row["name"]) for row in rows}
        missing = sorted(set(_TRIGGERS) - present)
        if missing:
            raise LifecycleError(
                code="LIFECYCLE_DB_TAMPERED",
                message=f"统一闭环账本 append-only 触发器缺失：{', '.join(missing)}",
                blocking=True,
                suggested_action="停止写入并人工核查 experience/research_lifecycle.sqlite。",
            )

    def add(self, record: Record) -> str:
        
        kind = object_type_of(record)
        object_id = object_id_of(record)
        if not object_id.strip():
            raise LifecycleError(
                code="LIFECYCLE_EMPTY_OBJECT_ID",
                message=f"{kind.value} 的对象 ID 不能为空",
                blocking=True,
                suggested_action="生成稳定且全局可追溯的对象 ID 后重试。",
            )
        payload = record.model_dump(mode="json")
        payload_text = _canonical(payload)
        with self._connect() as conn:
            if self._exists(conn, kind, object_id):
                raise LifecycleError(
                    code="LIFECYCLE_OBJECT_EXISTS",
                    message=f"对象已存在且不可覆盖：{kind.value}/{object_id}",
                    blocking=True,
                    suggested_action="创建新的不可变版本 ID；修正历史只能追加说明事件。",
                )
            for ref_type, ref_id in record_references(record):
                self._require(conn, ref_type, ref_id)
                if ref_type is ObjectType.DATA_SNAPSHOT and not isinstance(record, DataSnapshot):
                    snapshot = self._get_with_conn(conn, ref_type, ref_id)
                    if snapshot.quality_status is QualityStatus.BLOCKED:
                        raise LifecycleError(
                            code="DATA_SNAPSHOT_BLOCKED",
                            message=f"数据快照 {ref_id} 的质量状态为 blocked",
                            blocking=True,
                            suggested_action="修复数据质量并创建新的可用 DataSnapshot 后重试。",
                        )
            self._validate_record_context(conn, record)
            conn.execute(
                "INSERT INTO lifecycle_objects "
                "(object_type, object_id, created_at, schema_version, payload, content_hash) "
                "VALUES (?,?,?,?,?,?)",
                (
                    kind.value,
                    object_id,
                    record.created_at.isoformat(),
                    record.schema_version,
                    payload_text,
                    _digest(payload_text),
                ),
            )
            conn.commit()
        return object_id

    def _validate_record_context(self, conn: sqlite3.Connection, record: Record) -> None:
        program_id = getattr(record, "research_program_id", "")
        snapshot_id = getattr(record, "data_snapshot_id", "")
        if program_id and snapshot_id:
            program = self._get_with_conn(conn, ObjectType.RESEARCH_PROGRAM, program_id)
            if program.data_snapshot_id != snapshot_id:
                raise LifecycleError(
                    code="RESEARCH_PROGRAM_SNAPSHOT_MISMATCH",
                    message=f"对象数据快照 {snapshot_id} 与研究任务 {program_id} 不一致",
                    blocking=True,
                    suggested_action="使用 ResearchProgram 冻结的数据快照创建对象。",
                )
            protocol_hash = getattr(record, "protocol_hash", "")
            if protocol_hash and program.protocol_hash != protocol_hash:
                raise LifecycleError(
                    code="RESEARCH_PROGRAM_PROTOCOL_MISMATCH",
                    message=f"对象协议与研究任务 {program_id} 的冻结协议不一致",
                    blocking=True,
                    suggested_action="不得修改研究任务的冻结回测协议；按原协议重建运行。",
                )
        if isinstance(record, LockedTestExposure):
            previous = conn.execute(
                "SELECT object_id FROM lifecycle_objects "
                "WHERE object_type=? AND json_extract(payload, '$.subject_id')=? LIMIT 1",
                (ObjectType.LOCKED_TEST_EXPOSURE.value, record.subject_id),
            ).fetchone()
            if previous:
                raise LifecycleError(
                    code="LOCKED_TEST_ALREADY_EXPOSED",
                    message=f"主体版本 {record.subject_id} 已登记过锁定测试暴露",
                    context={"previous_exposure_id": previous["object_id"]},
                    blocking=True,
                    suggested_action="结束该候选；不得围绕已曝光区间继续调参或重复开封。",
                )
            run = self._get_with_conn(conn, ObjectType.EVALUATION_RUN, record.evaluation_run_id)
            if run.purpose is not RunPurpose.LOCKED_TEST:
                raise LifecycleError(
                    code="LOCKED_TEST_RUN_PURPOSE_MISMATCH",
                    message="LockedTestExposure 引用的运行不是 locked_test 用途",
                    blocking=True,
                    suggested_action="新建 purpose=locked_test 且口径一致的 EvaluationRun。",
                )
            mismatches = []
            for name in (
                "subject_type",
                "subject_id",
                "research_program_id",
                "data_snapshot_id",
                "protocol_hash",
                "window",
            ):
                if getattr(run, name) != getattr(record, name):
                    mismatches.append(name)
            if mismatches:
                raise LifecycleError(
                    code="LOCKED_TEST_REFERENCE_MISMATCH",
                    message=f"锁定测试暴露与运行引用不一致：{', '.join(mismatches)}",
                    blocking=True,
                    suggested_action="使用同一主体、数据快照、区间和 protocol 创建暴露台账。",
                )
        elif isinstance(record, FactorEvaluation):
            for run_id in record.evaluation_run_ids:
                run = self._get_with_conn(conn, ObjectType.EVALUATION_RUN, run_id)
                if (
                    run.subject_type.value != "factor"
                    or run.subject_id != record.factor_version_id
                    or run.data_snapshot_id != record.data_snapshot_id
                ):
                    raise LifecycleError(
                        code="FACTOR_EVALUATION_RUN_MISMATCH",
                        message=(
                            f"评价运行 {run_id} 与因子版本或数据快照不一致："
                            f"{record.factor_version_id}/{record.data_snapshot_id}"
                        ),
                        blocking=True,
                        suggested_action="只引用该因子版本的 EvaluationRun。",
                    )
                run_state = self._current_state_with_conn(conn, ObjectType.EVALUATION_RUN, run_id)
                if run_state != "completed":
                    raise LifecycleError(
                        code="FACTOR_EVALUATION_RUN_INCOMPLETE",
                        message=f"评价运行 {run_id} 当前为 {run_state}，尚未完成",
                        blocking=True,
                        suggested_action="先完成运行并冻结指标产物，再登记 FactorEvaluation。",
                    )
        elif isinstance(record, StrategyVersion):
            if record.parent_strategy_version_id:
                parent = self._get_with_conn(
                    conn, ObjectType.STRATEGY_VERSION, record.parent_strategy_version_id
                )
                changed = self._strategy_changed_dimensions(parent, record)
                if changed != {record.change_dimension}:
                    actual = ", ".join(sorted(item.value for item in changed)) or "无"
                    raise LifecycleError(
                        code="STRATEGY_MULTIPLE_CHANGE_DIMENSIONS",
                        message=(
                            f"策略版本声明变更 {record.change_dimension.value}，"
                            f"实际识别为：{actual}"
                        ),
                        context={"actual_dimensions": sorted(item.value for item in changed)},
                        blocking=True,
                        suggested_action="每轮只修改一类策略内容；将其他变化拆成后续子版本。",
                    )
        elif isinstance(record, ChampionDecision):
            champion = self._get_with_conn(
                conn, ObjectType.STRATEGY_VERSION, record.champion_strategy_version_id
            )
            challenger = self._get_with_conn(
                conn, ObjectType.STRATEGY_VERSION, record.challenger_strategy_version_id
            )
            rollback = self._get_with_conn(
                conn, ObjectType.STRATEGY_VERSION, record.rollback_strategy_version_id
            )
            names = {champion.strategy_name, challenger.strategy_name, rollback.strategy_name}
            if len(names) != 1:
                raise LifecycleError(
                    code="CHAMPION_DECISION_SLOT_MISMATCH",
                    message="champion、challenger 与回滚版本不属于同一策略槽位",
                    blocking=True,
                    suggested_action="只比较同一 strategy_name 下的不可变策略版本。",
                )

    @staticmethod
    def _strategy_changed_dimensions(
        parent: StrategyVersion, child: StrategyVersion
    ) -> set[StrategyChangeDimension]:
        changed: set[StrategyChangeDimension] = set()
        parent_inputs = {item.factor_version_id: item for item in parent.factor_inputs}
        child_inputs = {item.factor_version_id: item for item in child.factor_inputs}
        if set(parent_inputs) != set(child_inputs):
            changed.add(StrategyChangeDimension.FACTOR_SET)
        else:
            if any(parent_inputs[key].weight != child_inputs[key].weight for key in parent_inputs):
                changed.add(StrategyChangeDimension.FACTOR_WEIGHT)
            if any(
                parent_inputs[key].transformations != child_inputs[key].transformations
                for key in parent_inputs
            ):
                changed.add(StrategyChangeDimension.STANDARDIZATION)
            if any(
                parent_inputs[key].regime_weights != child_inputs[key].regime_weights
                for key in parent_inputs
            ):
                changed.add(StrategyChangeDimension.REGIME_WEIGHT)
        if parent.combination_policy != child.combination_policy:
            changed.add(StrategyChangeDimension.COMBINATION_POLICY)
        if parent.risk_policy != child.risk_policy:
            changed.add(StrategyChangeDimension.RISK_BUDGET)
        if parent.execution_policy != child.execution_policy:
            changed.add(StrategyChangeDimension.EXECUTION_POLICY)
        changed.update(
            LifecycleStore._portfolio_policy_dimensions(
                parent.portfolio_policy, child.portfolio_policy
            )
        )
        return changed

    @staticmethod
    def _portfolio_policy_dimensions(
        parent: dict[str, Any], child: dict[str, Any]
    ) -> set[StrategyChangeDimension]:
        key_dimensions = {
            "top_n": StrategyChangeDimension.SELECTION_POLICY,
            "ranking_buffer": StrategyChangeDimension.SELECTION_POLICY,
            "selection": StrategyChangeDimension.SELECTION_POLICY,
            "rebalance_frequency": StrategyChangeDimension.REBALANCE_POLICY,
            "minimum_holding_period": StrategyChangeDimension.REBALANCE_POLICY,
            "rebalance": StrategyChangeDimension.REBALANCE_POLICY,
            "single_name_cap": StrategyChangeDimension.EXPOSURE_LIMIT,
            "industry_cap": StrategyChangeDimension.EXPOSURE_LIMIT,
            "style_cap": StrategyChangeDimension.EXPOSURE_LIMIT,
            "exposure": StrategyChangeDimension.EXPOSURE_LIMIT,
            "turnover_cap": StrategyChangeDimension.TURNOVER_POLICY,
            "minimum_trade_amount": StrategyChangeDimension.TURNOVER_POLICY,
            "minimum_weight_change": StrategyChangeDimension.TURNOVER_POLICY,
            "turnover": StrategyChangeDimension.TURNOVER_POLICY,
            "cash_ratio": StrategyChangeDimension.CASH_POLICY,
            "cash_policy": StrategyChangeDimension.CASH_POLICY,
        }
        changed_keys = {
            key for key in set(parent) | set(child) if parent.get(key) != child.get(key)
        }
        unknown = sorted(changed_keys - set(key_dimensions))
        if unknown:
            raise LifecycleError(
                code="STRATEGY_POLICY_CHANGE_UNCLASSIFIED",
                message=f"组合政策存在无法归类的变更键：{', '.join(unknown)}",
                blocking=True,
                suggested_action="把字段归入 selection/rebalance/exposure/turnover/cash 标准分组。",
            )
        return {key_dimensions[key] for key in changed_keys}

    def get(self, object_type: ObjectType | str, object_id: str) -> Record:
        with self._connect() as conn:
            return self._get_with_conn(conn, ObjectType(object_type), object_id)

    def purge_account(self, account_id: str) -> None:
        """Explicit account erasure is the only exception to the append-only ledger."""
        from portfolio.deletion import _contains_account

        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)

        with self._connect() as conn:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("BEGIN IMMEDIATE")
            self._verify_triggers(conn)
            rows = conn.execute("SELECT object_type, object_id, payload FROM lifecycle_objects").fetchall()
            records = {(row['object_type'], row['object_id']): json.loads(row['payload']) for row in rows}
            deleted = {key for key, value in records.items() if _contains_account(value, account_id)}
            while deleted:
                refs = {object_id for _, object_id in deleted} | {f"lifecycle://{kind}/{object_id}" for kind, object_id in deleted}
                related = {key for key, value in records.items() if key not in deleted and any(ref in refs for ref in strings(value))}
                if not related:
                    break
                deleted.update(related)
            if not deleted:
                conn.commit()
                return
            conn.execute("CREATE TEMP TABLE account_erasure (kind TEXT, id TEXT, PRIMARY KEY(kind,id))")
            conn.executemany("INSERT INTO account_erasure VALUES (?,?)", sorted(deleted))
            # Drop and recreate deletion guards in one transaction; ordinary writes stay immutable.
            for name in ("trg_lifecycle_event_no_delete", "trg_lifecycle_object_no_delete"):
                conn.execute(f"DROP TRIGGER {name}")
            for table in ("lifecycle_events", "lifecycle_objects"):
                conn.execute(f"DELETE FROM {table} WHERE EXISTS (SELECT 1 FROM account_erasure WHERE kind=object_type AND id=object_id)")
            for name in ("trg_lifecycle_event_no_delete", "trg_lifecycle_object_no_delete"):
                conn.execute(_TRIGGERS[name])
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # Account-only report artifacts are no longer reachable through the ledger.
        retained = {ref for key, value in records.items() if key not in deleted for ref in strings(value)}
        root = workspace().root.resolve()
        for key in deleted:
            for field, ref in records[key].items():
                if field.endswith("artifact_ref") and isinstance(ref, str) and "://" not in ref and ref not in retained:
                    path = (root / ref).resolve()
                    if root in path.parents and path.is_file():
                        path.unlink()

    def _get_with_conn(
        self, conn: sqlite3.Connection, object_type: ObjectType, object_id: str
    ) -> Record:
        row = self._require(conn, object_type, object_id)
        return parse_record(object_type, json.loads(row["payload"]))

    def list_objects(
        self, object_type: ObjectType | str | None = None, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 5000:
            raise LifecycleError(
                code="LIFECYCLE_BAD_LIMIT",
                message="limit 必须在 1..5000 之间",
                blocking=True,
                suggested_action="缩小查询范围后重试。",
            )
        clauses = ""
        params: list[Any] = []
        if object_type is not None:
            clauses = "WHERE object_type=?"
            params.append(ObjectType(object_type).value)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT object_type, object_id, created_at, schema_version, content_hash "
                f"FROM lifecycle_objects {clauses} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [
                {
                    **dict(row),
                    "state": self._current_state_with_conn(
                        conn, ObjectType(row["object_type"]), str(row["object_id"])
                    ),
                }
                for row in rows
            ]

    def transition(
        self,
        object_type: ObjectType | str,
        object_id: str,
        target: str,
        *,
        actor_type: ActorType | str,
        actor_ref: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        
        with self._connect() as conn:
            event_id = self._transition_with_conn(
                conn,
                ObjectType(object_type),
                object_id,
                target,
                ActorType(actor_type),
                actor_ref,
                reason,
                payload or {},
            )
            conn.commit()
            return event_id

    def transition_batch(self, transitions: list[dict[str, Any]]) -> list[int]:
        
        if not transitions:
            raise LifecycleError(
                code="LIFECYCLE_TRANSITION_BATCH_EMPTY",
                message="原子状态流转不能为空。",
                blocking=True,
                suggested_action="至少提供一个完整的目标状态和审计身份。",
            )
        event_ids: list[int] = []
        with self._connect() as conn:
            for item in transitions:
                event_ids.append(
                    self._transition_with_conn(
                        conn,
                        ObjectType(item["object_type"]),
                        str(item["object_id"]),
                        str(item["target"]),
                        ActorType(item["actor_type"]),
                        str(item["actor_ref"]),
                        str(item["reason"]),
                        dict(item.get("payload") or {}),
                    )
                )
            conn.commit()
        return event_ids

    def _transition_with_conn(
        self,
        conn: sqlite3.Connection,
        kind: ObjectType,
        object_id: str,
        target: str,
        actor: ActorType,
        actor_ref: str,
        reason: str,
        payload: dict[str, Any],
    ) -> int:
        if not actor_ref.strip() or not reason.strip():
            raise LifecycleError(
                code="LIFECYCLE_TRANSITION_AUDIT_MISSING",
                message="状态流转必须记录 actor_ref 和 reason",
                blocking=True,
                suggested_action="补充操作者标识和可审计理由后重试。",
            )
        self._require(conn, kind, object_id)
        current = self._current_state_with_conn(conn, kind, object_id)
        validate_transition(kind, current, target, actor)
        self._validate_transition_context(conn, kind, object_id, target, payload)
        cursor = conn.execute(
            "INSERT INTO lifecycle_events "
            "(object_type, object_id, from_state, to_state, actor_type, actor_ref, "
            "reason, created_at, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                kind.value,
                object_id,
                current,
                target,
                actor.value,
                actor_ref.strip(),
                reason.strip(),
                _utcnow_iso(),
                _canonical(payload),
            ),
        )
        return int(cursor.lastrowid or 0)

    def _validate_transition_context(
        self,
        conn: sqlite3.Connection,
        kind: ObjectType,
        object_id: str,
        target: str,
        payload: dict[str, Any],
    ) -> None:
        if kind is ObjectType.EVALUATION_RUN and target == "completed":
            missing = [
                name
                for name in ("outcome", "metrics_digest", "artifact_hash")
                if not payload.get(name)
            ]
            if payload.get("outcome") not in {"passed", "invalid"}:
                missing.append("outcome=passed/invalid")
            if missing:
                raise LifecycleError(
                    code="EVALUATION_RUN_RESULT_MISSING",
                    message=f"完成评价运行缺少结果字段：{', '.join(missing)}",
                    blocking=True,
                    suggested_action="冻结指标产物后，在完成事件中写 outcome、metrics_digest、artifact_hash。",
                )
        if kind is ObjectType.EVALUATION_RUN and target == "failed" and not payload.get(
            "failure_code"
        ):
            raise LifecycleError(
                code="EVALUATION_RUN_FAILURE_CODE_MISSING",
                message="失败的评价运行必须记录 failure_code",
                blocking=True,
                suggested_action="写入结构化失败码和建议操作后再结束运行。",
            )
        if kind is ObjectType.LOCKED_TEST_EXPOSURE and target == "opened":
            exposure = self._get_with_conn(conn, kind, object_id)
            subject_kind = (
                ObjectType.FACTOR_VERSION
                if exposure.subject_type.value == "factor"
                else ObjectType.STRATEGY_VERSION
            )
            subject_state = self._current_state_with_conn(conn, subject_kind, exposure.subject_id)
            if subject_kind is ObjectType.STRATEGY_VERSION and subject_state != "frozen":
                raise LifecycleError(
                    code="LOCKED_TEST_CANDIDATE_NOT_FROZEN",
                    message=f"候选 {exposure.subject_id} 当前为 {subject_state}，不能开封锁定测试",
                    blocking=True,
                    suggested_action="先冻结候选定义及全部参数，再开封唯一一次锁定测试。",
                )
        if kind is ObjectType.LOCKED_TEST_EXPOSURE and target in {"completed", "failed"}:
            exposure = self._get_with_conn(conn, kind, object_id)
            run_state = self._current_state_with_conn(
                conn, ObjectType.EVALUATION_RUN, exposure.evaluation_run_id
            )
            required = "completed" if target == "completed" else "failed"
            if run_state != required:
                raise LifecycleError(
                    code="LOCKED_TEST_RUN_STATE_MISMATCH",
                    message=f"暴露结论为 {target}，但锁定测试运行状态为 {run_state}",
                    blocking=True,
                    suggested_action=f"先把关联 EvaluationRun 正确结束为 {required}。",
                )
        if kind is ObjectType.STRATEGY_VERSION and target == "challenger":
            if not self._has_completed_exposure(conn, object_id):
                raise LifecycleError(
                    code="STRATEGY_LOCKED_TEST_REQUIRED",
                    message=f"策略版本 {object_id} 尚无已完成的唯一锁定测试",
                    blocking=True,
                    suggested_action="完成唯一锁定测试后再进入 challenger 影子竞争。",
                )
        if kind is ObjectType.CHAMPION_DECISION and target == "approved":
            decision = self._get_with_conn(conn, kind, object_id)
            self._validate_champion_evidence(conn, decision)
        if kind is ObjectType.STRATEGY_VERSION and target == "champion":
            target_strategy = self._get_with_conn(conn, kind, object_id)
            report_rows = conn.execute(
                "SELECT object_id, payload FROM lifecycle_objects WHERE object_type=?",
                (ObjectType.RESEARCH_REPORT.value,),
            ).fetchall()
            report_approved = any(
                json.loads(row["payload"]).get("research_program_id")
                == target_strategy.research_program_id
                and self._current_state_with_conn(
                    conn,
                    ObjectType.RESEARCH_REPORT,
                    str(row["object_id"]),
                )
                == "approved"
                for row in report_rows
            )
            if not report_approved:
                raise LifecycleError(
                    code="STRATEGY_CHAMPION_REPORT_APPROVAL_REQUIRED",
                    message=f"策略版本 {object_id} 的研究报告尚未人工批准。",
                    blocking=True,
                    suggested_action="先核验并批准 ResearchReport，再执行 champion 切换。",
                )
            strategy_rows = conn.execute(
                "SELECT object_id, payload FROM lifecycle_objects WHERE object_type=?",
                (ObjectType.STRATEGY_VERSION.value,),
            ).fetchall()
            has_existing_champion = any(
                str(row["object_id"]) != object_id
                and json.loads(row["payload"]).get("strategy_name")
                == target_strategy.strategy_name
                and self._current_state_with_conn(
                    conn, ObjectType.STRATEGY_VERSION, str(row["object_id"])
                )
                == "champion"
                for row in strategy_rows
            )
            if not has_existing_champion:
                if not self._has_completed_run(conn, object_id, RunPurpose.SHADOW):
                    raise LifecycleError(
                        code="CHAMPION_SHADOW_RUN_REQUIRED",
                        message=f"策略版本 {object_id} 尚无已完成的影子运行",
                        blocking=True,
                        suggested_action="先完成配置要求的最小影子观察期，再由人工 bootstrap 晋升。",
                    )
                return
            rows = conn.execute(
                "SELECT object_id, payload FROM lifecycle_objects WHERE object_type=?",
                (ObjectType.CHAMPION_DECISION.value,),
            ).fetchall()
            approved = False
            for row in rows:
                payload = json.loads(row["payload"])
                if (
                    payload.get("challenger_strategy_version_id") != object_id
                    or payload.get("action") != "promote"
                ):
                    continue
                if (
                    self._current_state_with_conn(
                        conn, ObjectType.CHAMPION_DECISION, str(row["object_id"])
                    )
                    == "approved"
                ):
                    approved = True
                    break
            if not approved:
                raise LifecycleError(
                    code="CHAMPION_DECISION_REQUIRED",
                    message=f"策略版本 {object_id} 没有已批准的 ChampionDecision",
                    blocking=True,
                    suggested_action="先生成证据包并由人工批准晋升决定。",
                )
        if kind is ObjectType.PORTFOLIO_PLAN and target == "confirmed":
            plan = self._get_with_conn(conn, kind, object_id)
            state = self._current_state_with_conn(
                conn, ObjectType.STRATEGY_VERSION, plan.strategy_version_id
            )
            if state != "champion":
                raise LifecycleError(
                    code="PORTFOLIO_PLAN_NON_CHAMPION",
                    message=f"真实调仓计划只能确认 champion，当前策略状态为 {state}",
                    blocking=True,
                    suggested_action="challenger 仅运行影子计划；先完成人工晋升再生成真实计划。",
                )

    def _has_completed_exposure(self, conn: sqlite3.Connection, subject_id: str) -> bool:
        rows = conn.execute(
            "SELECT object_id, payload FROM lifecycle_objects WHERE object_type=?",
            (ObjectType.LOCKED_TEST_EXPOSURE.value,),
        ).fetchall()
        return any(
            json.loads(row["payload"]).get("subject_id") == subject_id
            and self._current_state_with_conn(
                conn, ObjectType.LOCKED_TEST_EXPOSURE, str(row["object_id"])
            )
            == "completed"
            for row in rows
        )

    def _has_completed_run(
        self, conn: sqlite3.Connection, subject_id: str, purpose: RunPurpose
    ) -> bool:
        rows = conn.execute(
            "SELECT object_id, payload FROM lifecycle_objects WHERE object_type=?",
            (ObjectType.EVALUATION_RUN.value,),
        ).fetchall()
        return any(
            json.loads(row["payload"]).get("subject_id") == subject_id
            and json.loads(row["payload"]).get("purpose") == purpose.value
            and self._current_state_with_conn(
                conn, ObjectType.EVALUATION_RUN, str(row["object_id"])
            )
            == "completed"
            for row in rows
        )

    def _validate_champion_evidence(
        self, conn: sqlite3.Connection, decision: ChampionDecision
    ) -> None:
        champion_state = self._current_state_with_conn(
            conn, ObjectType.STRATEGY_VERSION, decision.champion_strategy_version_id
        )
        challenger_state = self._current_state_with_conn(
            conn, ObjectType.STRATEGY_VERSION, decision.challenger_strategy_version_id
        )
        expected_target_state = (
            "watching"
            if decision.action is DecisionAction.ROLLBACK
            else "challenger"
        )
        if champion_state != "champion" or challenger_state != expected_target_state:
            raise LifecycleError(
                code="CHAMPION_DECISION_STATE_MISMATCH",
                message=(
                    "策略切换证据的对象状态不合法："
                    f"champion={champion_state}, target={challenger_state}，"
                    f"期望 target={expected_target_state}"
                ),
                blocking=True,
                suggested_action="刷新竞争面板，按当前 champion 和合法目标重新生成证据包。",
            )
        incomplete = [
            run_id
            for run_id in decision.evidence_run_ids
            if self._current_state_with_conn(conn, ObjectType.EVALUATION_RUN, run_id)
            != "completed"
        ]
        if incomplete:
            raise LifecycleError(
                code="CHAMPION_EVIDENCE_INCOMPLETE",
                message=f"晋升证据包含未完成运行：{', '.join(incomplete)}",
                blocking=True,
                suggested_action="等待全部历史、锁定测试和影子证据完成后再审批。",
            )
        runs = [
            self._get_with_conn(conn, ObjectType.EVALUATION_RUN, run_id)
            for run_id in decision.evidence_run_ids
        ]
        challenger_locked = any(
            run.subject_id == decision.challenger_strategy_version_id
            and run.purpose is RunPurpose.LOCKED_TEST
            for run in runs
        )
        champion_shadow = [
            run
            for run in runs
            if run.subject_id == decision.champion_strategy_version_id
            and run.purpose is RunPurpose.SHADOW
        ]
        challenger_shadow = [
            run
            for run in runs
            if run.subject_id == decision.challenger_strategy_version_id
            and run.purpose is RunPurpose.SHADOW
        ]
        same_shadow_caliber = any(
            left.data_snapshot_id == right.data_snapshot_id
            and left.window == right.window
            and left.protocol_hash == right.protocol_hash
            for left in champion_shadow
            for right in challenger_shadow
        )
        locked_required = decision.action is DecisionAction.PROMOTE
        if (locked_required and not challenger_locked) or not same_shadow_caliber:
            raise LifecycleError(
                code="CHAMPION_EVIDENCE_CALIBER_MISSING",
                message=(
                    "策略切换证据缺少同口径同期影子对比"
                    if not locked_required
                    else "晋升证据缺少 challenger 锁定测试或同口径同期影子对比"
                ),
                blocking=True,
                suggested_action=(
                    "补齐当前 champion 与回滚目标同快照、区间、协议的 shadow runs。"
                    if not locked_required
                    else "补齐 locked_test 与 champion/challenger 同快照、区间、协议的 shadow runs。"
                ),
            )

    def current_state(self, object_type: ObjectType | str, object_id: str) -> str:
        kind = ObjectType(object_type)
        with self._connect() as conn:
            self._require(conn, kind, object_id)
            return self._current_state_with_conn(conn, kind, object_id)

    @staticmethod
    def _current_state_with_conn(
        conn: sqlite3.Connection, object_type: ObjectType, object_id: str
    ) -> str:
        if object_type is ObjectType.FACTOR_VERSION:
            # Factor availability has one source of truth: the catalog's three
            # states. Historical research events never grant usage permission.
            row = conn.execute(
                "SELECT payload FROM lifecycle_objects WHERE object_type=? AND object_id=?",
                (object_type.value, object_id),
            ).fetchone()
            if row:
                from factors.registry import FactorRegistry
                name = json.loads(row["payload"])["factor_name"]
                return next((factor.status.value for factor in FactorRegistry().list(include_research=True)
                             if factor.name == name), "disabled")
            return "disabled"
        row = conn.execute(
            "SELECT to_state FROM lifecycle_events WHERE object_type=? AND object_id=? "
            "ORDER BY event_id DESC LIMIT 1",
            (object_type.value, object_id),
        ).fetchone()
        if row:
            return str(row["to_state"])
        if object_type is ObjectType.DATA_SNAPSHOT:
            object_row = conn.execute(
                "SELECT payload FROM lifecycle_objects WHERE object_type=? AND object_id=?",
                (object_type.value, object_id),
            ).fetchone()
            if object_row and json.loads(object_row["payload"]).get("quality_status") == "blocked":
                return "quarantined"
        return INITIAL_STATES[object_type]

    def events(self, object_type: ObjectType | str, object_id: str) -> list[dict[str, Any]]:
        kind = ObjectType(object_type)
        with self._connect() as conn:
            self._require(conn, kind, object_id)
            rows = conn.execute(
                "SELECT * FROM lifecycle_events WHERE object_type=? AND object_id=? "
                "ORDER BY event_id",
                (kind.value, object_id),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                result.append(item)
            return result

    def list_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        
        if limit < 1 or limit > 5000:
            raise LifecycleError(
                code="LIFECYCLE_BAD_EVENT_LIMIT",
                message="事件 limit 必须在 1..5000 之间。",
                blocking=True,
                suggested_action="缩小审计事件查询范围。",
            )
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lifecycle_events ORDER BY event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                self._require(
                    conn, ObjectType(str(row["object_type"])), str(row["object_id"])
                )
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                result.append(item)
            return result

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT object_type, COUNT(*) AS count FROM lifecycle_objects GROUP BY object_type"
            ).fetchall()
            counts = {kind.value: 0 for kind in ObjectType}
            counts.update({str(row["object_type"]): int(row["count"]) for row in rows})
            return {
                "object_counts": counts,
                "total_objects": sum(counts.values()),
                "total_events": int(
                    conn.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0]
                ),
                "db_path": str(self.db_path),
            }

    def verify_integrity(self) -> dict[str, int]:
        
        checked = 0
        references = 0
        checked_events = 0
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM lifecycle_objects ORDER BY created_at").fetchall()
            for row in rows:
                if _digest(str(row["payload"])) != row["content_hash"]:
                    raise LifecycleError(
                        code="LIFECYCLE_CONTENT_HASH_MISMATCH",
                        message=f"对象内容哈希不匹配：{row['object_type']}/{row['object_id']}",
                        blocking=True,
                        suggested_action="停止使用该账本并进行人工审计。",
                    )
                record = parse_record(row["object_type"], json.loads(row["payload"]))
                for ref_type, ref_id in record_references(record):
                    self._require(conn, ref_type, ref_id)
                    references += 1
                checked += 1
            expected_states: dict[tuple[ObjectType, str], str] = {}
            event_rows = conn.execute(
                "SELECT * FROM lifecycle_events ORDER BY event_id"
            ).fetchall()
            for row in event_rows:
                kind = ObjectType(str(row["object_type"]))
                object_id = str(row["object_id"])
                self._require(conn, kind, object_id)
                key = (kind, object_id)
                expected = expected_states.get(key, "proposed" if kind is ObjectType.FACTOR_VERSION else INITIAL_STATES[kind])
                if str(row["from_state"]) != expected:
                    raise LifecycleError(
                        code="LIFECYCLE_EVENT_CHAIN_BROKEN",
                        message=f"状态事件链断裂：{kind.value}/{object_id}",
                        context={
                            "event_id": int(row["event_id"]),
                            "expected_from": expected,
                            "actual_from": str(row["from_state"]),
                        },
                        blocking=True,
                        suggested_action="停止写入并人工审计 lifecycle_events。",
                    )
                actor = ActorType(str(row["actor_type"]))
                if kind is not ObjectType.FACTOR_VERSION:
                    validate_transition(kind, expected, str(row["to_state"]), actor)
                if not str(row["actor_ref"]).strip() or not str(row["reason"]).strip():
                    raise LifecycleError(
                        code="LIFECYCLE_EVENT_AUDIT_MISSING",
                        message=f"状态事件缺少审计身份或理由：event_id={row['event_id']}",
                        blocking=True,
                        suggested_action="停止使用该账本并核查事件来源。",
                    )
                expected_states[key] = str(row["to_state"])
                checked_events += 1

            champions: dict[str, str] = {}
            exposure_subjects: dict[str, str] = {}
            for row in rows:
                kind = ObjectType(str(row["object_type"]))
                object_id = str(row["object_id"])
                record = parse_record(kind, json.loads(row["payload"]))
                state = expected_states.get((kind, object_id), INITIAL_STATES[kind])
                if isinstance(record, StrategyVersion) and state == "champion":
                    previous = champions.setdefault(record.strategy_name, object_id)
                    if previous != object_id:
                        raise LifecycleError(
                            code="LIFECYCLE_MULTIPLE_CHAMPIONS",
                            message=f"策略槽位 {record.strategy_name} 同时存在多个 champion。",
                            context={"champions": [previous, object_id]},
                            blocking=True,
                            suggested_action="停止生成真实计划并人工核查切换事件。",
                        )
                if isinstance(record, LockedTestExposure):
                    previous = exposure_subjects.setdefault(record.subject_id, object_id)
                    if previous != object_id:
                        raise LifecycleError(
                            code="LIFECYCLE_DUPLICATE_LOCKED_EXPOSURE",
                            message=f"主体 {record.subject_id} 存在重复锁定测试曝光。",
                            context={"exposures": [previous, object_id]},
                            blocking=True,
                            suggested_action="停止研究并人工核查锁定测试台账。",
                        )
        return {
            "checked_objects": checked,
            "checked_references": references,
            "checked_events": checked_events,
            "champion_slots": len(champions),
        }

    @staticmethod
    def _exists(conn: sqlite3.Connection, object_type: ObjectType, object_id: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM lifecycle_objects WHERE object_type=? AND object_id=?",
                (object_type.value, object_id),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _require(
        conn: sqlite3.Connection, object_type: ObjectType, object_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM lifecycle_objects WHERE object_type=? AND object_id=?",
            (object_type.value, object_id),
        ).fetchone()
        if row is None:
            raise LifecycleError(
                code="LIFECYCLE_REFERENCE_NOT_FOUND",
                message=f"找不到引用对象：{object_type.value}/{object_id}",
                context={"object_type": object_type.value, "object_id": object_id},
                blocking=True,
                suggested_action="先登记被引用的不可变对象，再登记当前对象。",
            )
        return row

__all__ = ["LifecycleStore"]
