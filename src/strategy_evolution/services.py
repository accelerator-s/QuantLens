from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Any

import yaml

from backtest.config import BacktestConfig
from backtest.engine import run_backtest
from backtest.protocol import verify_protocol_or_raise
from backtest.report import BacktestRunStore
from backtest.signal import RuntimeFactor
from exceptions import ContractViolation, LifecycleError, QuantTrainingError
from factor_evolution.models import FoldResult, WindowResult
from factor_evolution.walk_forward import assess_generalization, build_folds
from lifecycle.models import (
    DataSnapshot,
    EvaluationRun,
    FactorVersion,
    LockedTestExposure,
    ObjectType,
    ResearchBudget,
    ResearchLoop,
    ResearchProgram,
    RunPurpose,
    StrategyChangeDimension,
    StrategyFactorInput,
    StrategyVersion,
    SubjectType,
    TimeWindow,
)
from lifecycle.states import ActorType
from lifecycle.store import LifecycleStore
from paths import workspace
from proposals import ProposalSource, ProposalStore
from strategy import ResolvedStrategyProfile, StrategyProfileStore

from .models import StrategyCandidateResult, StrategyEvolutionConfig, StrategyRunResult

def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _metric(metrics: dict, group: str, key: str) -> float:
    return float((metrics.get(group) or {}).get(key) or 0)

def _window(raw) -> TimeWindow:
    return TimeWindow(start=raw.start, end=raw.end)

class _LogConsole:
    def __init__(self, emit) -> None:
        self.emit = emit

    def print(self, *objects, **_kwargs) -> None:
        self.emit(" ".join(str(item) for item in objects))

_PROPOSE_SYSTEM = """你是多因子策略研究助手。输出 StructuredConfig，config_kind 固定为
strategy_evolution_candidate。proposed_value 必须包含 change_dimension、change_summary、
hypothesis、factor_inputs、combination_policy、portfolio_policy、risk_policy、
execution_policy。必须完整复制父版本，每轮只改一个 change_dimension。
不得改数据切分、锁定测试、费用口径、交易规则或验收阈值。不得伪造回测数值。
允许的维度：factor_set、factor_weight、standardization、combination_policy、
regime_weight、selection_policy、rebalance_policy、exposure_limit、turnover_policy、
risk_budget、cash_policy。因子只能引用 allowed_factor_versions 中的 ID。"""

_REVIEW_SYSTEM = """你是严格的策略审查员。输出 StructuredConfig，config_kind 固定为
strategy_evolution_review。proposed_value 包含 decision（continue/stop/freeze）、
assessment、failure_reason 和 next_change。只能依据给定的训练、walk-forward、
父版本差值和因子剔除结果；不得要求查看或改写锁定测试。"""

_AI_CONTRACT_ATTEMPTS = 3

def _run_structured_with_retry(
    config: StrategyEvolutionConfig,
    *,
    prompt: str,
    system: str,
    expected_kind: str,
) -> dict[str, Any]:
    
    from ai.router import AIRouter

    router = AIRouter(request_timeout=180)
    selected = router.pick("structured_config", model_name=config.model_name or None)
    base_prompt = prompt
    last_error: ContractViolation | None = None
    for attempt in range(1, _AI_CONTRACT_ATTEMPTS + 1):
        try:
            payload = router.run_task(
                task="structured_config",
                prompt=prompt,
                system=system,
                model_name=selected.name,
            )
            if payload.get("config_kind") != expected_kind:
                raise ContractViolation(
                    code="STRATEGY_EVOLUTION_CONFIG_KIND_INVALID",
                    message=f"AI 返回的 config_kind 应为 {expected_kind}。",
                    blocking=False,
                )
            return payload
        except ContractViolation as exc:
            last_error = exc
            if attempt >= _AI_CONTRACT_ATTEMPTS:
                break
            prompt = (
                f"{base_prompt}\n\n上一次输出未通过结构化契约："
                f"[{exc.code}] {exc.message[:2500]}\n"
                f"这是同一轮的格式纠错。保持原金融假设和所有数值，"
                f"根对象只输出 config_kind={expected_kind}、proposed_value 和 open_questions，"
                "删除 schema 之外的字段后重新输出。"
            )
    raise ContractViolation(
        code="STRATEGY_EVOLUTION_OUTPUT_RETRY_EXHAUSTED",
        message=f"AI 连续 {_AI_CONTRACT_ATTEMPTS} 次未生成合规输出，本轮已留档。",
        context={
            "model_name": selected.name,
            "attempts": _AI_CONTRACT_ATTEMPTS,
            "last_error_code": last_error.code if last_error else "",
        },
        blocking=False,
        suggested_action="保持同一模型与严格 schema，由后续策略研究轮次继续。",
    )

class StrategyEvolutionServices:
    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()
        self.profile_store = StrategyProfileStore()

    def begin(
        self, config: StrategyEvolutionConfig, snapshot: DataSnapshot, *, program_id: str
    ) -> ResearchProgram:
        protocol = verify_protocol_or_raise()
        if not protocol.lock.locked:
            raise QuantTrainingError(
                code="STRATEGY_EVOLUTION_PROTOCOL_NOT_LOCKED",
                message="多因子进化要求回测协议已锁定。",
                blocking=True,
                suggested_action="先在管理页完成回测协议锁定。",
            )
        program = ResearchProgram(
            program_id=program_id,
            loop=ResearchLoop.STRATEGY,
            objective=f"自动优化多因子策略 {config.profile_name}",
            hypothesis_scope=[
                "单轮单类策略变更",
                "每轮因子剔除实验",
                "walk-forward 样本外稳定性",
                "候选冻结后唯一锁定测试",
            ],
            data_snapshot_id=snapshot.snapshot_id,
            protocol_hash=protocol.content_hash(),
            train_window=_window(config.train_window),
            validation_window=_window(config.validation_window),
            locked_test_window=_window(config.test_window),
            embargo_days=config.walk_forward.embargo_days,
            targets=config.targets.model_dump(mode="json"),
            configuration=config.model_dump(mode="json"),
            budget=ResearchBudget(
                max_iterations=config.max_iterations,
                
                max_model_calls=config.max_iterations * 2 * _AI_CONTRACT_ATTEMPTS,
                max_runtime_seconds=config.max_runtime_seconds,
            ),
            random_seed=config.random_seed,
        )
        self.store.add(program)
        self.transition(ObjectType.RESEARCH_PROGRAM, program_id, "queued", "WebUI 创建策略研究")
        return program

    def baseline(self, config: StrategyEvolutionConfig, program: ResearchProgram) -> StrategyVersion:
        profile = self.profile_store.resolve(config.profile_name)
        factor_inputs = self._profile_factor_inputs(profile)
        payload = self._policy_payload(profile)
        semantic = {
            "strategy_name": config.profile_name,
            "program_id": program.program_id,
            "factor_inputs": [item.model_dump(mode="json") for item in factor_inputs],
            **payload,
        }
        record = StrategyVersion(
            strategy_version_id=f"SV-{_hash(semantic)[:20].upper()}",
            strategy_name=config.profile_name,
            research_program_id=program.program_id,
            data_snapshot_id=program.data_snapshot_id,
            factor_inputs=factor_inputs,
            **payload,
            change_dimension=StrategyChangeDimension.INITIAL,
            change_summary="冻结当前已解析 profile 作为基线",
            hypothesis="当前多因子配置作为所有候选的唯一父基线",
        )
        self.store.add(record)
        return record

    def propose(
        self,
        config: StrategyEvolutionConfig,
        parent: StrategyVersion,
        history: list[dict[str, Any]],
        *,
        iteration: int,
    ) -> StrategyVersion:
        allowed = self.available_factor_versions()
        prompt = json.dumps(
            {
                "iteration": iteration,
                "fixed_windows": {
                    "train": config.train_window.model_dump(mode="json"),
                    "validation": config.validation_window.model_dump(mode="json"),
                    "locked_test": "hidden_until_candidate_frozen",
                },
                "targets": config.targets.model_dump(mode="json"),
                "parent": self._editable(parent),
                "allowed_factor_versions": allowed,
                "history_without_locked_test": history[-8:],
                "verified_live_feedback": self.learning_context(
                    parent.strategy_name
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        result = _run_structured_with_retry(
            config,
            prompt=prompt,
            system=_PROPOSE_SYSTEM,
            expected_kind="strategy_evolution_candidate",
        )
        value = result.get("proposed_value") or {}
        editable = {
            key: value.get(key)
            for key in (
                "factor_inputs",
                "combination_policy",
                "portfolio_policy",
                "risk_policy",
                "execution_policy",
                "change_dimension",
                "change_summary",
                "hypothesis",
            )
        }
        digest = _hash({"parent": parent.strategy_version_id, **editable})
        candidate = StrategyVersion.model_validate(
            {
                "strategy_version_id": f"SV-{digest[:20].upper()}",
                "strategy_name": parent.strategy_name,
                "parent_strategy_version_id": parent.strategy_version_id,
                "research_program_id": parent.research_program_id,
                "data_snapshot_id": parent.data_snapshot_id,
                **editable,
            }
        )
        self._validate_factor_ids(candidate)
        ProposalStore().create(
            role="structured_config",
            source=ProposalSource.API_MODEL,
            payload=result,
            model_name=str(result.get("model_name") or ""),
        )
        self.store.add(candidate)
        return candidate

    def evaluate(
        self,
        config: StrategyEvolutionConfig,
        candidate: StrategyVersion,
        *,
        emit,
    ) -> StrategyCandidateResult:
        folds = build_folds(config)  # 只依赖同名科学字段
        fold_results: list[FoldResult] = []
        train_runs: list[StrategyRunResult] = []
        validation_runs: list[StrategyRunResult] = []
        for fold in folds:
            train = self.run_window(
                config, candidate, fold.train_window, RunPurpose.TRAIN, emit=emit
            )
            valid = self.run_window(
                config, candidate, fold.validation_window, RunPurpose.WALK_FORWARD, emit=emit
            )
            train_runs.append(train)
            validation_runs.append(valid)
            fold_results.append(
                FoldResult(
                    fold_id=fold.fold_id,
                    train_window=fold.train_window,
                    validation_window=fold.validation_window,
                    train=WindowResult(
                        window="train", run_id=train.run_id, metrics=train.metrics
                    ),
                    validation=WindowResult(
                        window="validation", run_id=valid.run_id, metrics=valid.metrics
                    ),
                )
            )
        assessment = assess_generalization(config, fold_results)
        aggregate = self._aggregate(validation_runs)
        passed = self._passes(config, aggregate, assessment)
        return StrategyCandidateResult(
            strategy_version_id=candidate.strategy_version_id,
            train_runs=train_runs,
            validation_runs=validation_runs,
            aggregate_metrics=aggregate,
            positive_fold_ratio=assessment.validation_positive_fold_ratio,
            oos_stability=assessment.median_fold_stability,
            constraints_passed=passed,
            score=self._score(aggregate, assessment.median_fold_stability),
        )

    def run_window(
        self,
        config: StrategyEvolutionConfig,
        candidate: StrategyVersion,
        window,
        purpose: RunPurpose,
        *,
        emit,
    ) -> StrategyRunResult:
        reusable = self._completed_window(candidate, purpose, window)
        if reusable is not None:
            emit(
                f"复用已冻结 {purpose.value} artifact：{reusable.run_id} "
                f"({window.start}..{window.end})"
            )
            return reusable
        protocol = verify_protocol_or_raise()
        profile = self.profile_for(candidate)
        runtime = self.runtime_factors(candidate)
        run_id = f"BT-SE-{uuid.uuid4().hex[:20].upper()}"
        evaluation_run_id = f"ER-{run_id}"
        artifact_ref = str(
            (workspace().runs_backtest / profile.profile_name / run_id).relative_to(
                workspace().root
            )
        )
        self.store.add(
            EvaluationRun(
                evaluation_run_id=evaluation_run_id,
                subject_type=SubjectType.STRATEGY,
                subject_id=candidate.strategy_version_id,
                research_program_id=candidate.research_program_id,
                data_snapshot_id=candidate.data_snapshot_id,
                purpose=purpose,
                window=_window(window),
                protocol_hash=protocol.content_hash(),
                artifact_ref=artifact_ref,
            )
        )
        self.transition(
            ObjectType.EVALUATION_RUN,
            evaluation_run_id,
            "running",
            "评价定义和 artifact 目标路径已冻结",
        )
        exposure: LockedTestExposure | None = None
        if purpose is RunPurpose.LOCKED_TEST:
            exposure_id = f"LTE-{_hash([candidate.strategy_version_id, evaluation_run_id])[:20].upper()}"
            exposure = LockedTestExposure(
                exposure_id=exposure_id,
                subject_type=SubjectType.STRATEGY,
                subject_id=candidate.strategy_version_id,
                research_program_id=candidate.research_program_id,
                data_snapshot_id=candidate.data_snapshot_id,
                evaluation_run_id=evaluation_run_id,
                window=_window(window),
                protocol_hash=protocol.content_hash(),
                candidate_frozen_at=datetime.now().astimezone(),
            )
            self.store.add(exposure)
            self.transition(
                ObjectType.LOCKED_TEST_EXPOSURE,
                exposure_id,
                "opened",
                "候选已冻结；在读取锁定区间前永久开封唯一测试",
            )
        try:
            outcome = run_backtest(
                BacktestConfig(
                    profile_name=config.profile_name,
                    start=window.start,
                    end=window.end,
                    initial_capital=Decimal(str(config.initial_capital)),
                    lookback_buffer_days=config.lookback_buffer_days,
                ),
                protocol=protocol,
                profile=profile,
                runtime_factors=runtime,
                console=_LogConsole(emit),
                run_id=run_id,
            )
            _artifact_ref, artifact_hash = self._run_artifact(outcome.run_id)
            self.transition(
                ObjectType.EVALUATION_RUN,
                evaluation_run_id,
                "completed",
                "指标 artifact 已冻结",
                payload={
                    "outcome": "passed",
                    "metrics_digest": _hash(outcome.metrics),
                    "artifact_hash": artifact_hash,
                },
            )
        except Exception as exc:
            failure_code = str(getattr(exc, "code", type(exc).__name__))
            self.transition(
                ObjectType.EVALUATION_RUN,
                evaluation_run_id,
                "failed",
                "回测在完成不可变指标前失败",
                payload={"failure_code": failure_code},
            )
            if exposure is not None:
                self.transition(
                    ObjectType.LOCKED_TEST_EXPOSURE,
                    exposure.exposure_id,
                    "failed",
                    "锁定测试已开封但运行失败，禁止再次测试",
                )
            raise
        return StrategyRunResult(
            run_id=outcome.run_id,
            purpose=purpose.value,
            window=window,
            metrics=outcome.metrics,
            evaluation_run_id=evaluation_run_id,
            locked_test_exposure_id=exposure.exposure_id if exposure else "",
        )

    def ablation(
        self,
        config: StrategyEvolutionConfig,
        candidate: StrategyVersion,
        *,
        emit,
    ) -> tuple[StrategyVersion | None, list[StrategyRunResult]]:
        if len(candidate.factor_inputs) < 2:
            return None, []
        removed = min(candidate.factor_inputs, key=lambda item: abs(item.weight))
        remaining = [item for item in candidate.factor_inputs if item != removed]
        total = sum(item.weight for item in remaining)
        if total <= 0:
            raise ContractViolation(
                code="STRATEGY_ABLATION_WEIGHT_INVALID",
                message="因子剔除后权重合计不为正数。",
                blocking=True,
                suggested_action="修正候选权重后恢复。",
            )
        inputs = [
            item.model_copy(update={"weight": item.weight / total}) for item in remaining
        ]
        editable = self._editable(candidate)
        editable["factor_inputs"] = [item.model_dump(mode="json") for item in inputs]
        editable["change_dimension"] = StrategyChangeDimension.FACTOR_SET.value
        editable["change_summary"] = f"剔除最低权重因子 {removed.factor_version_id}"
        editable["hypothesis"] = "检验当前策略增益是否真的来自该因子"
        digest = _hash({"parent": candidate.strategy_version_id, **editable})
        record = StrategyVersion.model_validate(
            {
                "strategy_version_id": f"SV-{digest[:20].upper()}",
                "strategy_name": candidate.strategy_name,
                "parent_strategy_version_id": candidate.strategy_version_id,
                "research_program_id": candidate.research_program_id,
                "data_snapshot_id": candidate.data_snapshot_id,
                **editable,
            }
        )
        try:
            existing = self.store.get(
                ObjectType.STRATEGY_VERSION, record.strategy_version_id
            )
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_REFERENCE_NOT_FOUND":
                raise
            self.store.add(record)
            existing = record
        assert isinstance(existing, StrategyVersion)
        state = self.store.current_state(
            ObjectType.STRATEGY_VERSION, existing.strategy_version_id
        )
        if state == "proposed":
            self.transition(
                ObjectType.STRATEGY_VERSION,
                existing.strategy_version_id,
                "evaluating",
                "开始因子剔除实验",
            )
        runs = [
            self.run_window(
                config,
                existing,
                fold.validation_window,
                RunPurpose.WALK_FORWARD,
                emit=emit,
            )
            for fold in build_folds(config)
        ]
        if self.store.current_state(
            ObjectType.STRATEGY_VERSION, existing.strategy_version_id
        ) == "evaluating":
            self.transition(
                ObjectType.STRATEGY_VERSION,
                existing.strategy_version_id,
                "rejected",
                "剔除实验仅作增量证据",
            )
        return existing, runs

    def review(
        self,
        config: StrategyEvolutionConfig,
        candidate: StrategyVersion,
        result: StrategyCandidateResult,
        parent_result: StrategyCandidateResult,
    ) -> dict[str, Any]:
        payload = _run_structured_with_retry(
            config,
            prompt=json.dumps(
                {
                    "change": {
                        "dimension": candidate.change_dimension.value,
                        "summary": candidate.change_summary,
                        "hypothesis": candidate.hypothesis,
                    },
                    "candidate": result.model_dump(mode="json"),
                    "parent": parent_result.model_dump(mode="json"),
                    "targets": config.targets.model_dump(mode="json"),
                    "locked_test": "not_visible",
                },
                ensure_ascii=False,
                indent=2,
            ),
            system=_REVIEW_SYSTEM,
            expected_kind="strategy_evolution_review",
        )
        ProposalStore().create(
            role="structured_config",
            source=ProposalSource.API_MODEL,
            payload=payload,
            model_name=str(payload.get("model_name") or ""),
        )
        value = payload.get("proposed_value") or {}
        decision = value.get("decision")
        if decision not in {"continue", "stop", "freeze"}:
            raise ContractViolation(
                code="STRATEGY_EVOLUTION_REVIEW_DECISION_INVALID",
                message=f"AI 策略复盘决策无效：{decision}",
                blocking=False,
                suggested_action="decision 必须为 continue、stop 或 freeze。",
            )
        return value

    def locked_exposure(
        self,
        strategy: StrategyVersion,
        run: StrategyRunResult,
        *,
        passed: bool,
    ) -> LockedTestExposure:
        if not run.locked_test_exposure_id:
            raise LifecycleError(
                code="STRATEGY_LOCKED_EXPOSURE_NOT_RESERVED",
                message="策略锁定测试没有在读取测试数据前登记暴露。",
                blocking=True,
                suggested_action="终止该候选；不得事后补登记或重复执行锁定测试。",
            )
        record = self.store.get(
            ObjectType.LOCKED_TEST_EXPOSURE, run.locked_test_exposure_id
        )
        assert isinstance(record, LockedTestExposure)
        self.transition(
            ObjectType.LOCKED_TEST_EXPOSURE,
            record.exposure_id,
            "completed",
            "锁定测试完成",
            payload={"passed": passed},
        )
        return record

    def profile_for(self, strategy: StrategyVersion) -> ResolvedStrategyProfile:
        base = self.profile_store.resolve(strategy.strategy_name)
        payload = base.model_dump(mode="json")
        names = self._factor_names(strategy)
        payload["factors"] = {
            "weighting": "manual",
            "items": [
                {"name": names[item.factor_version_id], "weight": str(item.weight)}
                for item in strategy.factor_inputs
            ],
        }
        chains = {tuple(item.transformations) for item in strategy.factor_inputs}
        if len(chains) != 1:
            raise ContractViolation(
                code="STRATEGY_ENGINE_PER_FACTOR_TRANSFORM_UNSUPPORTED",
                message="当前回测引擎要求各因子使用同一标准化链。",
                blocking=False,
                suggested_action="本轮只提议全局 standardization 变体。",
            )
        payload["standardization_chain"] = [
            json.loads(token) for token in next(iter(chains), ())
        ]
        portfolio = strategy.portfolio_policy
        payload["position_constraint"].update(
            {
                "top_n": portfolio["top_n"],
                "max_weight_per_name": str(portfolio["single_name_cap"]),
                "min_cash_ratio": str(portfolio["cash_ratio"]),
            }
        )
        payload["rebalance_frequency"] = portfolio["rebalance_frequency"]
        payload["holding_period"] = portfolio["minimum_holding_period"]
        risk = strategy.risk_policy
        payload["risk_control"].update(
            {
                "max_single_name_pct": str(risk["max_single_name_pct"]),
                "max_industry_pct": str(risk["max_industry_pct"]),
            }
        )
        return ResolvedStrategyProfile.model_validate(payload)

    def runtime_factors(self, strategy: StrategyVersion) -> dict[str, RuntimeFactor]:
        result: dict[str, RuntimeFactor] = {}
        for item in strategy.factor_inputs:
            factor = self.store.get(ObjectType.FACTOR_VERSION, item.factor_version_id)
            assert isinstance(factor, FactorVersion)
            artifact = self._factor_artifact(factor)
            result[factor.factor_name] = RuntimeFactor(
                source=artifact["code"],
                dependencies=tuple(factor.dependencies),
                direction=str(artifact.get("direction") or "undecided"),
            )
        return result

    def learning_context(self, strategy_name: str) -> list[dict[str, Any]]:
        
        context = []
        for row in self.store.list_objects(ObjectType.LEARNING_EVENT, limit=5000):
            if row["state"] not in {"verified", "accepted"}:
                continue
            event = self.store.get(ObjectType.LEARNING_EVENT, row["object_id"])
            related = False
            for ref in event.source_object_refs:
                if ref.object_type is not ObjectType.ATTRIBUTION_RECORD:
                    continue
                attribution = self.store.get(ref.object_type, ref.object_id)
                strategy = self.store.get(
                    ObjectType.STRATEGY_VERSION,
                    attribution.strategy_version_id,
                )
                if strategy.strategy_name == strategy_name:
                    related = True
                    break
            if related:
                context.append(
                    {
                        "learning_event_id": event.learning_event_id,
                        "state": row["state"],
                        "category": event.category,
                        "observation": event.observation,
                        "confidence": event.confidence,
                        "recommended_action": event.recommended_action,
                        "evidence_refs": event.evidence_refs,
                    }
                )
        return context[:20]

    def available_factor_versions(self) -> list[dict[str, Any]]:
        result = []
        for row in self.store.list_objects(ObjectType.FACTOR_VERSION, limit=5000):
            if row["state"] not in {"shadow", "production"}:
                continue
            factor = self.store.get(ObjectType.FACTOR_VERSION, row["object_id"])
            assert isinstance(factor, FactorVersion)
            result.append(
                {
                    "factor_version_id": factor.factor_version_id,
                    "factor_name": factor.factor_name,
                    "state": row["state"],
                    "hypothesis": factor.hypothesis,
                    "transformations": factor.transformations,
                }
            )
        return result

    def _profile_factor_inputs(self, profile: ResolvedStrategyProfile) -> list[StrategyFactorInput]:
        available = self.available_factor_versions()
        by_name: dict[str, dict] = {}
        for item in available:
            by_name.setdefault(item["factor_name"], item)
        missing = [item.name for item in profile.factors.items if item.name not in by_name]
        if missing:
            raise LifecycleError(
                code="STRATEGY_FACTOR_VERSION_NOT_READY",
                message=f"策略引用的因子尚无 shadow/production 版本：{'、'.join(missing)}",
                context={"missing_factors": missing},
                blocking=True,
                suggested_action="先在实验页对这些因子完成 AI 进化和唯一锁定测试。",
            )
        raw_weights = [
            float(item.weight) if item.weight is not None else 1.0
            for item in profile.factors.items
        ]
        total = sum(raw_weights)
        chain = [
            json.dumps(step.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for step in profile.standardization_chain
        ]
        return [
            StrategyFactorInput(
                factor_version_id=by_name[item.name]["factor_version_id"],
                weight=weight / total,
                transformations=chain,
            )
            for item, weight in zip(profile.factors.items, raw_weights, strict=True)
        ]

    @staticmethod
    def _policy_payload(profile: ResolvedStrategyProfile) -> dict[str, Any]:
        return {
            "combination_policy": {"weighting": profile.factors.weighting.value},
            "portfolio_policy": {
                "top_n": profile.position_constraint.top_n,
                "rebalance_frequency": profile.rebalance_frequency.value,
                "minimum_holding_period": profile.holding_period,
                "single_name_cap": float(profile.position_constraint.max_weight_per_name),
                "cash_ratio": float(profile.position_constraint.min_cash_ratio),
            },
            "risk_policy": {
                "max_single_name_pct": float(profile.risk_control.max_single_name_pct),
                "max_industry_pct": float(profile.risk_control.max_industry_pct),
            },
            "execution_policy": {
                "price_anchor": profile.execution_assumption.price_anchor,
                "fill_assumption": profile.execution_assumption.fill_assumption,
                "fee_source": profile.fees_slippage.fee_source,
                "slippage_bps": float(profile.fees_slippage.slippage_bps),
                "price_deviation_threshold": float(profile.price_deviation_threshold),
            },
        }

    @staticmethod
    def _editable(strategy: StrategyVersion) -> dict[str, Any]:
        return {
            "factor_inputs": [item.model_dump(mode="json") for item in strategy.factor_inputs],
            "combination_policy": strategy.combination_policy,
            "portfolio_policy": strategy.portfolio_policy,
            "risk_policy": strategy.risk_policy,
            "execution_policy": strategy.execution_policy,
            "change_dimension": strategy.change_dimension.value,
            "change_summary": strategy.change_summary,
            "hypothesis": strategy.hypothesis,
        }

    def _validate_factor_ids(self, strategy: StrategyVersion) -> None:
        allowed = {
            row["factor_version_id"] for row in self.available_factor_versions()
        }
        unknown = sorted(
            item.factor_version_id
            for item in strategy.factor_inputs
            if item.factor_version_id not in allowed
        )
        if unknown:
            raise ContractViolation(
                code="STRATEGY_FACTOR_VERSION_NOT_ALLOWED",
                message=f"AI 引用了未获得 shadow/production 权限的因子版本：{'、'.join(unknown)}",
                blocking=False,
                suggested_action="下一轮只使用 allowed_factor_versions。",
            )

    def _factor_names(self, strategy: StrategyVersion) -> dict[str, str]:
        result = {}
        for item in strategy.factor_inputs:
            factor = self.store.get(ObjectType.FACTOR_VERSION, item.factor_version_id)
            assert isinstance(factor, FactorVersion)
            result[item.factor_version_id] = factor.factor_name
        return result

    @staticmethod
    def _factor_artifact(factor: FactorVersion) -> dict[str, Any]:
        path = workspace().root / factor.code_ref
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleError(
                code="STRATEGY_FACTOR_ARTIFACT_INVALID",
                message=f"因子版本 artifact 无法读取：{factor.code_ref}",
                context={"error": str(exc)},
                blocking=True,
                suggested_action="恢复该因子版本的不可变代码 artifact。",
            ) from exc
        if not isinstance(raw.get("code"), str):
            raise LifecycleError(
                code="STRATEGY_FACTOR_CODE_MISSING",
                message=f"因子版本 {factor.factor_version_id} 没有可执行代码。",
                blocking=True,
                suggested_action="先修复因子研究产物完整性。",
            )
        return raw

    @staticmethod
    def _aggregate(runs: list[StrategyRunResult]) -> dict[str, Any]:
        excess = [_metric(item.metrics, "returns", "annualized_excess_return") for item in runs]
        sharpe = [_metric(item.metrics, "risk", "sharpe") for item in runs]
        drawdown = [_metric(item.metrics, "risk", "max_drawdown") for item in runs]
        turnover = [
            _metric(item.metrics, "trading", "annualized_turnover_oneside") for item in runs
        ]
        return {
            "returns": {"annualized_excess_return": median(excess)},
            "risk": {"sharpe": median(sharpe), "max_drawdown": min(drawdown)},
            "trading": {"annualized_turnover_oneside": max(turnover)},
            "walk_forward": {"run_ids": [item.run_id for item in runs], "fold_excess": excess},
        }

    @staticmethod
    def _passes(config, metrics: dict, assessment) -> bool:
        target = config.targets
        return (
            _metric(metrics, "returns", "annualized_excess_return")
            >= target.min_annualized_excess_return
            and _metric(metrics, "risk", "sharpe") >= target.min_sharpe
            and abs(_metric(metrics, "risk", "max_drawdown")) <= target.max_drawdown
            and _metric(metrics, "trading", "annualized_turnover_oneside")
            <= target.max_annualized_turnover
            and assessment.validation_positive_fold_ratio >= target.min_positive_fold_ratio
            and assessment.median_fold_stability >= target.min_oos_stability
        )

    @staticmethod
    def _score(metrics: dict, stability: float) -> float:
        return (
            _metric(metrics, "returns", "annualized_excess_return")
            + 0.05 * _metric(metrics, "risk", "sharpe")
            - 0.20 * abs(_metric(metrics, "risk", "max_drawdown"))
            - 0.002 * _metric(metrics, "trading", "annualized_turnover_oneside")
            + 0.03 * stability
        )

    @staticmethod
    def ablation_delta(
        candidate: StrategyCandidateResult, ablation_runs: list[StrategyRunResult]
    ) -> dict[str, Any]:
        if not ablation_runs:
            return {}
        ablation = StrategyEvolutionServices._aggregate(ablation_runs)
        return {
            "annualized_excess_return": _metric(
                candidate.aggregate_metrics, "returns", "annualized_excess_return"
            )
            - _metric(ablation, "returns", "annualized_excess_return"),
            "sharpe": _metric(candidate.aggregate_metrics, "risk", "sharpe")
            - _metric(ablation, "risk", "sharpe"),
            "max_drawdown": _metric(candidate.aggregate_metrics, "risk", "max_drawdown")
            - _metric(ablation, "risk", "max_drawdown"),
            "annualized_turnover": _metric(
                candidate.aggregate_metrics, "trading", "annualized_turnover_oneside"
            )
            - _metric(ablation, "trading", "annualized_turnover_oneside"),
        }

    @staticmethod
    def _run_artifact(run_id: str) -> tuple[str, str]:
        run = BacktestRunStore.open_run(run_id)
        payload = {}
        for name in ("config.yaml", "status.yaml", "metrics.yaml"):
            path = run.run_dir / name
            if path.exists():
                payload[name] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return str(run.run_dir.relative_to(workspace().root)), _hash(payload)

    def _completed_window(
        self,
        candidate: StrategyVersion,
        purpose: RunPurpose,
        window,
    ) -> StrategyRunResult | None:
        
        expected_start = str(window.start)
        expected_end = str(window.end)
        for row in self.store.list_objects(ObjectType.EVALUATION_RUN, limit=5000):
            if row["state"] != "completed":
                continue
            record = self.store.get(ObjectType.EVALUATION_RUN, row["object_id"])
            assert isinstance(record, EvaluationRun)
            if (
                record.subject_id != candidate.strategy_version_id
                or record.research_program_id != candidate.research_program_id
                or record.purpose is not purpose
                or record.window.start.isoformat() != expected_start
                or record.window.end.isoformat() != expected_end
            ):
                continue
            run_id = record.evaluation_run_id.removeprefix("ER-")
            _artifact_ref, current_hash = self._run_artifact(run_id)
            completed = next(
                (
                    event["payload"]
                    for event in reversed(
                        self.store.events(
                            ObjectType.EVALUATION_RUN, record.evaluation_run_id
                        )
                    )
                    if event["to_state"] == "completed"
                ),
                {},
            )
            expected_hash = str(completed.get("artifact_hash") or record.artifact_hash)
            if not expected_hash or current_hash != expected_hash:
                raise LifecycleError(
                    code="STRATEGY_RESUME_ARTIFACT_HASH_MISMATCH",
                    message=f"评价运行 {record.evaluation_run_id} 的 artifact 已改变。",
                    context={
                        "expected_hash": expected_hash,
                        "actual_hash": current_hash,
                    },
                    blocking=True,
                    suggested_action="不要续跑该研究；先核对运行目录的完整性。",
                )
            metrics = BacktestRunStore.open_run(run_id).read_metrics()
            if not metrics:
                raise LifecycleError(
                    code="STRATEGY_RESUME_METRICS_MISSING",
                    message=f"评价运行 {record.evaluation_run_id} 缺少指标。",
                    blocking=True,
                    suggested_action="恢复不可变运行 artifact 后再续跑。",
                )
            return StrategyRunResult(
                run_id=run_id,
                purpose=purpose.value,
                window=window,
                metrics=metrics,
                evaluation_run_id=record.evaluation_run_id,
                locked_test_exposure_id=self._exposure_id(record.subject_id),
            )
        return None

    def _exposure_id(self, subject_id: str) -> str:
        for row in self.store.list_objects(
            ObjectType.LOCKED_TEST_EXPOSURE, limit=5000
        ):
            exposure = self.store.get(
                ObjectType.LOCKED_TEST_EXPOSURE, row["object_id"]
            )
            assert isinstance(exposure, LockedTestExposure)
            if exposure.subject_id == subject_id:
                return exposure.exposure_id
        return ""

    def evaluation_complete(
        self, config: StrategyEvolutionConfig, candidate: StrategyVersion
    ) -> bool:
        
        for fold in build_folds(config):
            if self._completed_window(
                candidate, RunPurpose.TRAIN, fold.train_window
            ) is None:
                return False
            if self._completed_window(
                candidate, RunPurpose.WALK_FORWARD, fold.validation_window
            ) is None:
                return False
        return True

    def transition(
        self,
        kind: ObjectType,
        object_id: str,
        target: str,
        reason: str,
        *,
        payload: dict[str, Any] | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_ref: str = "strategy_evolution",
    ) -> None:
        if self.store.current_state(kind, object_id) == target:
            return
        self.store.transition(
            kind,
            object_id,
            target,
            actor_type=actor_type,
            actor_ref=actor_ref,
            reason=reason,
            payload=payload,
        )

__all__ = ["StrategyEvolutionServices"]
