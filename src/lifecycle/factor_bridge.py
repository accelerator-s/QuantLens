from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import yaml

from backtest.protocol import load_protocol
from backtest.report import BacktestRunStore
from exceptions import LifecycleError
from factor_evolution.models import EvolutionConfig
from factor_evolution.store import EvolutionStore
from paths import workspace

from .models import (
    DataSnapshot,
    EvaluationRun,
    FactorEvaluation,
    FactorVersion,
    LockedTestExposure,
    ObjectType,
    ResearchBudget,
    ResearchLoop,
    ResearchProgram,
    RunPurpose,
    SubjectType,
    TimeWindow,
)
from .factor_research import FactorResearchIndex
from .states import ActorType
from .store import LifecycleStore

def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _window(raw: dict[str, Any]) -> TimeWindow:
    return TimeWindow(start=raw["start"], end=raw["end"])

def _scalar_metrics(metrics: dict[str, Any], prefix: str = "") -> dict[str, float | int | str]:
    """Keep the original metric paths while satisfying the ledger's scalar schema."""
    result: dict[str, float | int | str] = {}
    for key, value in metrics.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_scalar_metrics(value, path))
        elif isinstance(value, (float, int, str)):
            result[path] = value
    return result

class FactorEvolutionBridge:

    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    @staticmethod
    def program_id(research_id: str) -> str:
        return f"RP-{research_id}"

    @staticmethod
    def factor_version_id(research_id: str, version_id: str) -> str:
        return f"FV-{research_id}-{version_id}"

    def begin(
        self, research_id: str, config: EvolutionConfig, snapshot: DataSnapshot
    ) -> ResearchProgram:
        from factor_evolution.publication import capture_catalog_base

        capture_catalog_base(EvolutionStore(), research_id)
        protocol = load_protocol()
        program = ResearchProgram(
            program_id=self.program_id(research_id),
            loop=ResearchLoop.FACTOR,
            objective=f"自动改进因子 {config.base_factor} 的样本外增量价值",
            hypothesis_scope=[
                "单轮单维变更",
                "walk-forward 泛化",
                "唯一锁定测试",
                "扣费后收益、回撤、换手与稳定性联合约束",
            ],
            data_snapshot_id=snapshot.snapshot_id,
            protocol_hash=protocol.content_hash(),
            train_window=TimeWindow(
                start=config.train_window.start, end=config.train_window.end
            ),
            validation_window=TimeWindow(
                start=config.validation_window.start, end=config.validation_window.end
            ),
            locked_test_window=TimeWindow(
                start=config.test_window.start, end=config.test_window.end
            ),
            embargo_days=config.walk_forward.embargo_days,
            targets=config.targets.model_dump(mode="json"),
            configuration=config.model_dump(mode="json"),
            budget=ResearchBudget(
                max_iterations=config.max_iterations,
                
                max_model_calls=max(6, config.max_iterations * 6),
                max_runtime_seconds=config.max_runtime_seconds,
            ),
            random_seed=config.random_seed,
        )
        self._add(program)
        self._transition(
            ObjectType.RESEARCH_PROGRAM,
            program.program_id,
            "queued",
            reason="WebUI 已创建自动因子研究任务",
        )
        return program

    def mark_running(self, research_id: str) -> None:
        program_id = self.program_id(research_id)
        if self.store.current_state(ObjectType.RESEARCH_PROGRAM, program_id) == "paused":
            self._transition(
                ObjectType.RESEARCH_PROGRAM,
                program_id,
                "queued",
                reason="用户从最后完整候选和回测 artifact 恢复",
            )
        self._transition(
            ObjectType.RESEARCH_PROGRAM,
            program_id,
            "running",
            reason="后台 AI 因子研究开始执行",
        )

    def mark_failed(self, research_id: str, *, code: str, message: str) -> None:
        program_id = self.program_id(research_id)
        current = self.store.current_state(ObjectType.RESEARCH_PROGRAM, program_id)
        if current in {"completed", "failed", "cancelled"}:
            return
        if current == "queued":
            self.mark_running(research_id)
        self._transition(
            ObjectType.RESEARCH_PROGRAM,
            program_id,
            "failed",
            reason=message,
            payload={"failure_code": code},
        )

    def ingest(self, research_id: str, *, narrative_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        legacy = EvolutionStore().detail(research_id)
        program_id = self.program_id(research_id)
        program = self.store.get(ObjectType.RESEARCH_PROGRAM, program_id)
        snapshot_id = program.data_snapshot_id
        config = legacy["config"]
        protocol_hash = program.protocol_hash

        versions = self._register_versions(
            research_id, program_id, snapshot_id, legacy["versions"]
        )
        run_ids_by_version = self._register_runs(
            research_id, program_id, snapshot_id, protocol_hash, legacy
        )
        evaluations = self._register_evaluations(
            research_id, snapshot_id, legacy, run_ids_by_version
        )
        exposure_id = self._register_locked_exposure(
            research_id, program_id, snapshot_id, protocol_hash, legacy
        )

        decision = legacy["status"].get("decision")
        program_state = self.store.current_state(ObjectType.RESEARCH_PROGRAM, program_id)
        if program_state == "running":
            if decision == "frozen":
                self._transition(
                    ObjectType.RESEARCH_PROGRAM,
                    program_id,
                    "frozen",
                    reason="候选与测试结论均已冻结",
                )
        from .reporting import ResearchReportService

        report = ResearchReportService(self.store).generate(program_id, narrative_payload=narrative_payload)
        from factor_evolution.publication import apply_accepted_version

        catalog_update = apply_accepted_version(EvolutionStore(), research_id)
        if self.store.current_state(ObjectType.RESEARCH_PROGRAM, program_id) in {
            "running",
            "frozen",
        }:
            self._transition(
                ObjectType.RESEARCH_PROGRAM,
                program_id,
                "completed",
                reason=(
                    legacy["status"].get("terminal_reason")
                    or "因子研究、锁定测试及数值引用报告已完成"
                ),
            )
        return {
            "program_id": program_id,
            "factor_versions": versions,
            "factor_evaluations": evaluations,
            "evaluation_runs": sum(len(items) for items in run_ids_by_version.values()),
            "locked_test_exposure_id": exposure_id,
            "research_report_id": report.research_report_id,
            "catalog_update": catalog_update,
        }

    def ingest_failure(self, research_id: str) -> dict[str, Any]:
        
        legacy = EvolutionStore().detail(research_id)
        program_id = self.program_id(research_id)
        program = self.store.get(ObjectType.RESEARCH_PROGRAM, program_id)
        snapshot_id = program.data_snapshot_id
        protocol_hash = program.protocol_hash
        versions = self._register_versions(
            research_id, program_id, snapshot_id, legacy["versions"]
        )
        run_ids = self._register_runs(
            research_id, program_id, snapshot_id, protocol_hash, legacy
        )
        evaluations = self._register_evaluations(
            research_id, snapshot_id, legacy, run_ids
        )
        exposure_id = self._register_locked_exposure(
            research_id, program_id, snapshot_id, protocol_hash, legacy
        )
        return {
            "program_id": program_id,
            "factor_versions": versions,
            "factor_evaluations": evaluations,
            "locked_test_exposure_id": exposure_id,
        }

    def _register_versions(
        self,
        research_id: str,
        program_id: str,
        snapshot_id: str,
        versions: list[dict[str, Any]],
    ) -> list[str]:
        pending = {item["version_id"]: item for item in versions}
        registered: list[str] = []
        while pending:
            progressed = False
            for legacy_id, item in list(pending.items()):
                parent_legacy = item.get("parent_version_id")
                if parent_legacy and parent_legacy in pending:
                    continue
                object_id = self.factor_version_id(research_id, legacy_id)
                record = FactorVersion(
                    factor_version_id=object_id,
                    factor_name=item["factor_name"],
                    parent_factor_version_id=(
                        self.factor_version_id(research_id, parent_legacy)
                        if parent_legacy
                        else None
                    ),
                    research_program_id=program_id,
                    data_snapshot_id=snapshot_id,
                    hypothesis=item.get("assumption") or item.get("change_summary") or "基础因子",
                    dependencies=item["dependencies"],
                    code_ref=(
                        f"runs/factor_evolution/{research_id}/versions/{legacy_id}.json"
                    ),
                    code_hash=_hash(item["code"]),
                    parameters=item.get("parameters") or {},
                    transformations=item.get("standardization") or [],
                    change_dimension=item.get("change_type") or "baseline",
                    change_summary=item.get("change_summary") or "基础版本",
                    generated_by=item.get("model_name") or "system",
                )
                self._add(record)
                registered.append(object_id)
                pending.pop(legacy_id)
                progressed = True
            if not progressed:
                raise LifecycleError(
                    code="FACTOR_VERSION_LINEAGE_BROKEN",
                    message=f"研究 {research_id} 的因子版本父链无法解析",
                    blocking=True,
                    suggested_action="检查 versions/*.json 的 parent_version_id。",
                )
        return registered

    def _register_runs(
        self,
        research_id: str,
        program_id: str,
        snapshot_id: str,
        protocol_hash: str,
        legacy: dict[str, Any],
    ) -> dict[str, list[str]]:
        specs: dict[str, tuple[str, RunPurpose, TimeWindow, dict[str, Any]]] = {}
        for event in legacy["events"]:
            if event["event_type"] == "training_evaluated":
                payload = event["payload"]
                train = payload["train"]
                specs[train["run_id"]] = (
                    payload["version_id"], RunPurpose.TRAIN,
                    _window(legacy["config"]["train_window"]), train["metrics"],
                )
        for event in legacy["events"]:
            if event["event_type"] != "version_evaluated":
                continue
            payload = event["payload"]
            version_id = payload["version_id"]
            for fold in payload.get("folds") or []:
                for side, purpose in (
                    ("train", RunPurpose.TRAIN),
                    ("validation", RunPurpose.WALK_FORWARD),
                ):
                    result = fold[side]
                    window = _window(fold[f"{side}_window"])
                    specs[result["run_id"]] = (version_id, purpose, window, result["metrics"])
        for event in legacy["events"]:
            if event["event_type"] != "locked_test_completed":
                continue
            payload = event["payload"]
            specs[payload["run_id"]] = (
                payload["version_id"],
                RunPurpose.LOCKED_TEST,
                _window(legacy["config"]["test_window"]),
                payload.get("metrics") or {},
            )

        result: dict[str, list[str]] = {}
        for run_id, (legacy_version, purpose, window, metrics) in specs.items():
            canonical_id = f"ER-{run_id}"
            subject_id = self.factor_version_id(research_id, legacy_version)
            artifact_ref, artifact_hash = self._run_artifact(run_id)
            metrics_digest = _hash(metrics)
            record = EvaluationRun(
                evaluation_run_id=canonical_id,
                subject_type=SubjectType.FACTOR,
                subject_id=subject_id,
                research_program_id=program_id,
                data_snapshot_id=snapshot_id,
                purpose=purpose,
                window=window,
                protocol_hash=protocol_hash,
                artifact_ref=artifact_ref,
                artifact_hash=artifact_hash,
            )
            self._add(record)
            if self.store.current_state(
                ObjectType.EVALUATION_RUN, canonical_id
            ) == "planned":
                self._transition(
                    ObjectType.EVALUATION_RUN,
                    canonical_id,
                    "running",
                    reason="迁移既有回测运行输入",
                )
            if self.store.current_state(
                ObjectType.EVALUATION_RUN, canonical_id
            ) == "running":
                self._transition(
                    ObjectType.EVALUATION_RUN,
                    canonical_id,
                    "completed",
                    reason="回测指标 artifact 已冻结",
                    payload={
                        "outcome": "passed",
                        "metrics_digest": metrics_digest,
                        "artifact_hash": artifact_hash,
                    },
                )
            result.setdefault(legacy_version, []).append(canonical_id)
        return result

    def _register_evaluations(
        self,
        research_id: str,
        snapshot_id: str,
        legacy: dict[str, Any],
        run_ids_by_version: dict[str, list[str]],
    ) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        scores: dict[str, float] = {}
        index = FactorResearchIndex(self.store)
        for event in legacy["events"]:
            if event["event_type"] != "version_evaluated":
                continue
            payload = event["payload"]
            legacy_id = payload["version_id"]
            if legacy_id in seen or not run_ids_by_version.get(legacy_id):
                continue
            seen.add(legacy_id)
            factor_id = self.factor_version_id(research_id, legacy_id)
            generalization = payload.get("generalization") or {}
            factor = self.store.get(ObjectType.FACTOR_VERSION, factor_id)
            assert isinstance(factor, FactorVersion)
            score = float(payload.get("score") or 0)
            parent_legacy_id = next(
                (
                    item.get("parent_version_id")
                    for item in legacy["versions"]
                    if item["version_id"] == legacy_id
                ),
                None,
            )
            validation_metrics = (payload.get("validation") or {}).get("metrics") or {}
            similarity = index.assess_similarity(factor)
            multiple_testing = index.multiple_testing(
                trial_index=len(result) + 1,
                session_trials=int(legacy["status"].get("iteration", 0)),
                fold_excess_returns=[
                    float(
                        ((fold.get("validation") or {}).get("metrics") or {})
                        .get("returns", {})
                        .get("annualized_excess_return", 0)
                    )
                    for fold in payload.get("folds") or []
                ],
            )
            parent_score = scores.get(parent_legacy_id or "")
            score_delta = score - parent_score if parent_score is not None else None
            duplicate = bool(
                similarity.get("duplicate_blocked")
                or (
                    parent_score is not None
                    and similarity.get("signal_redundancy_candidate")
                    and score_delta is not None
                    and score_delta <= 0.005
                )
            )
            evaluation_id = f"FE-{research_id}-{legacy_id}"
            evaluation = FactorEvaluation(
                factor_evaluation_id=evaluation_id,
                factor_version_id=factor_id,
                data_snapshot_id=snapshot_id,
                evaluation_run_ids=sorted(set(run_ids_by_version[legacy_id])),
                primary_metrics=_scalar_metrics(validation_metrics),
                annual_stability={"oos_stability": payload.get("stability") or 0},
                decay_metrics={"recent_degradation": generalization.get("recent_degradation", 0)},
                similarity=similarity,
                multiple_testing=multiple_testing,
                incremental_value=index.incremental_value(
                    score=score,
                    parent_score=parent_score,
                    validation_metrics=validation_metrics,
                ),
                verdict=(
                    "eliminate"
                    if duplicate
                    else "freeze" if payload.get("constraints_passed") else "continue"
                ),
                reason=(
                    "与已有因子定义重复或验证期横截面信号高相关"
                    if duplicate
                    else generalization.get("classification") or "评价完成"
                ),
            )
            self._add(evaluation)
            scores[legacy_id] = score
            result.append(evaluation_id)
        return result

    def _register_locked_exposure(
        self,
        research_id: str,
        program_id: str,
        snapshot_id: str,
        protocol_hash: str,
        legacy: dict[str, Any],
    ) -> str:
        reserved = next(
            (
                event
                for event in legacy["events"]
                if event["event_type"] == "locked_test_reserved"
            ),
            None,
        )
        if reserved is None:
            return ""
        completed = next(
            (
                event
                for event in legacy["events"]
                if event["event_type"] in {"locked_test_completed", "locked_test_failed"}
            ),
            None,
        )
        if completed is None:
            completed = {
                "event_type": "locked_test_failed",
                "payload": {
                    "code": "LOCKED_TEST_PROCESS_INTERRUPTED",
                    "message": "锁定测试开封后进程中断",
                },
            }
        if completed["event_type"] == "locked_test_completed" and not completed["payload"].get("run_id"):
            return ""
        payload = reserved["payload"]
        factor_id = self.factor_version_id(research_id, payload["version_id"])
        exposure_id = payload["exposure_id"]
        evaluation_run_id = f"ER-{completed['payload'].get('run_id', '')}"
        if completed["event_type"] == "locked_test_failed":
            evaluation_run_id = f"ER-LTF-{_hash([research_id, exposure_id])[:20].upper()}"
            failed_run = EvaluationRun(
                evaluation_run_id=evaluation_run_id,
                subject_type=SubjectType.FACTOR,
                subject_id=factor_id,
                research_program_id=program_id,
                data_snapshot_id=snapshot_id,
                purpose=RunPurpose.LOCKED_TEST,
                window=_window(payload["window"]),
                protocol_hash=protocol_hash,
                artifact_ref=f"runs/factor_evolution/{research_id}",
                artifact_hash=_hash(completed),
            )
            self._add(failed_run)
            if self.store.current_state(
                ObjectType.EVALUATION_RUN, evaluation_run_id
            ) == "planned":
                self._transition(
                    ObjectType.EVALUATION_RUN,
                    evaluation_run_id,
                    "running",
                    reason="锁定测试已开封并开始执行",
                )
            if self.store.current_state(
                ObjectType.EVALUATION_RUN, evaluation_run_id
            ) == "running":
                self._transition(
                    ObjectType.EVALUATION_RUN,
                    evaluation_run_id,
                    "failed",
                    reason=completed["payload"].get("message") or "锁定测试运行失败",
                    payload={
                        "failure_code": completed["payload"].get("code")
                        or "LOCKED_TEST_FAILED"
                    },
                )
        record = LockedTestExposure(
            exposure_id=exposure_id,
            subject_type=SubjectType.FACTOR,
            subject_id=factor_id,
            research_program_id=program_id,
            data_snapshot_id=snapshot_id,
            evaluation_run_id=evaluation_run_id,
            window=_window(payload["window"]),
            protocol_hash=protocol_hash,
            candidate_frozen_at=datetime.fromisoformat(reserved["created_at"]),
        )
        self._add(record)
        if self.store.current_state(
            ObjectType.LOCKED_TEST_EXPOSURE, exposure_id
        ) == "reserved":
            self._transition(
                ObjectType.LOCKED_TEST_EXPOSURE,
                exposure_id,
                "opened",
                reason="候选冻结后开封唯一锁定测试",
            )
        terminal = (
            "completed"
            if completed["event_type"] == "locked_test_completed"
            else "failed"
        )
        if self.store.current_state(
            ObjectType.LOCKED_TEST_EXPOSURE, exposure_id
        ) == "opened":
            self._transition(
                ObjectType.LOCKED_TEST_EXPOSURE,
                exposure_id,
                terminal,
                reason=(
                    "锁定测试运行已完成"
                    if terminal == "completed"
                    else "锁定测试已开封但运行失败，禁止再次测试"
                ),
                payload={"passed": completed["payload"].get("passed")},
            )
        return exposure_id

    def _run_artifact(self, run_id: str) -> tuple[str, str]:
        run = BacktestRunStore.open_run(run_id)
        files: dict[str, Any] = {}
        for name in ("config.yaml", "status.yaml", "metrics.yaml"):
            path = run.run_dir / name
            if path.exists():
                files[name] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return str(run.run_dir.relative_to(workspace().root)), _hash(files)

    def _exists(self, object_type: ObjectType, object_id: str) -> bool:
        try:
            self.store.get(object_type, object_id)
            return True
        except LifecycleError as exc:
            if exc.code == "LIFECYCLE_REFERENCE_NOT_FOUND":
                return False
            raise

    def _add(self, record) -> None:
        try:
            self.store.add(record)
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_OBJECT_EXISTS":
                raise

    def _transition(
        self,
        object_type: ObjectType,
        object_id: str,
        target: str,
        *,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.store.current_state(object_type, object_id) == target:
            return
        self.store.transition(
            object_type,
            object_id,
            target,
            actor_type=ActorType.SYSTEM,
            actor_ref="factor_evolution_bridge",
            reason=reason,
            payload=payload,
        )

__all__ = ["FactorEvolutionBridge"]
