from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from exceptions import QuantTrainingError

from .locked_test import LockedTestLedger
from .models import (
    EvolutionConfig,
    EvolutionDecision,
    EvolutionState,
    FactorVersion,
    FoldResult,
    ResearchWindow,
    ReviewResult,
    VersionResult,
)
from .store import EvolutionStore
from .walk_forward import aggregate_results, assess_generalization, build_folds

ProgressFn = Callable[[str], None]

class EvolutionServices(Protocol):
    def load_base(self, config: EvolutionConfig) -> FactorVersion: ...

    def propose(
        self,
        config: EvolutionConfig,
        current: FactorVersion,
        memory: list[dict],
        *,
        iteration: int,
        timeout_seconds: float,
    ) -> FactorVersion: ...

    def check_candidate(
        self, config: EvolutionConfig, version: FactorVersion, *, timeout_seconds: float
    ) -> None: ...

    def evaluate(
        self,
        config: EvolutionConfig,
        version: FactorVersion,
        *,
        window_name: str,
        window: ResearchWindow | None,
        timeout_seconds: float,
        on_progress: ProgressFn,
    ): ...

    def begin_experiment(
        self,
        config: EvolutionConfig,
        version: FactorVersion,
        parent_experiment_id: str,
    ) -> str: ...

    def record_result(
        self,
        experiment_id: str,
        result: VersionResult,
        *,
        improved: bool | None,
    ) -> None: ...

    def abort_experiment(self, experiment_id: str, *, reason: str, code: str) -> None: ...

    def review(
        self,
        config: EvolutionConfig,
        version: FactorVersion,
        result: VersionResult,
        best_result: VersionResult,
        *,
        timeout_seconds: float,
    ) -> ReviewResult: ...

    def record_review(
        self, experiment_id: str, review: ReviewResult, *, improved: bool
    ) -> None: ...

    def record_locked_test(
        self,
        experiment_id: str,
        result: VersionResult,
        *,
        passed: bool,
        stability: float,
    ) -> None: ...

class _StopRequested(Exception):
    pass

class EvolutionEngine:
    def __init__(self, store: EvolutionStore, services: EvolutionServices) -> None:
        self.store = store
        self.services = services
        self.locked_tests = LockedTestLedger(store.ws)

    def create(self, config: EvolutionConfig) -> str:
        try:
            build_folds(config)
        except ValueError as exc:
            raise QuantTrainingError(
                code="EVOLUTION_WALK_FORWARD_CONFIG_INVALID",
                message=f"滚动验证配置非法：{exc}",
                blocking=True,
                suggested_action="检查首个训练窗终点、验证起点、embargo 和滚动步长。",
            ) from exc
        self.locked_tests.assert_available(config.base_factor, config.test_window)
        return self.store.create(config).research_id

    def run(self, research_id: str, *, on_progress: ProgressFn | None = None) -> dict:
        def emit(message: str) -> None:
            self.store.append_event(research_id, "runtime_log", {"message": message})
            if on_progress:
                on_progress(message)
        if hasattr(on_progress, 'progress'):
            emit.progress = on_progress.progress
        config = self.store.load_config(research_id)
        status = self.store.load_status(research_id)
        if status.state in {EvolutionState.COMPLETED, EvolutionState.CANCELLED}:
            return self.store.detail(research_id)
        prior_events = self.store.detail(research_id)["events"]
        if any(event["event_type"] == "locked_test_reserved" for event in prior_events):
            raise QuantTrainingError(
                code="EVOLUTION_LOCKED_TEST_TERMINAL",
                message="该会话已经暴露锁定测试，禁止恢复后继续修改因子。",
                context={"research_id": research_id},
                blocking=True,
                suggested_action=(
                    "保留本会话记录；新建研究时把旧测试区间降级为训练或验证，并指定新锁定区间。"
                ),
            )
        if any(event["event_type"] in {"finalist_frozen", "version_evaluated"} for event in prior_events):
            raise QuantTrainingError(
                code="EVOLUTION_VALIDATION_TERMINAL",
                message="该会话已冻结候选或读取验证结果，禁止恢复搜索。",
                blocking=True,
                suggested_action="核对已有结果；新的研究必须重新登记搜索预算，不能把验证结果反馈给模型。",
            )
        latest_iteration_event = next((event for event in reversed(prior_events)
                                       if event["event_type"] in {"candidate_created", "candidate_invalidated", "round_completed", "round_failed"}
                                       and event["payload"].get("iteration") == status.iteration), None)
        invalidated = latest_iteration_event is not None and latest_iteration_event["event_type"] == "candidate_invalidated"
        if status.iteration > 0 and (invalidated or not any(
            event["event_type"] in {"candidate_created", "round_completed", "round_failed"}
            and event["payload"].get("iteration") == status.iteration
            for event in prior_events
        )):
            # Restart a proposal interrupted before any candidate was saved.
            # It must not silently consume a research round on process recovery.
            self.store.append_event(research_id, "proposal_restarted", {"iteration": status.iteration})
            status.current_version_id = status.best_version_id
            status.iteration -= 1
        started = time.monotonic()
        prior_elapsed = status.elapsed_seconds
        if status.started_at is None:
            status.started_at = datetime.now(tz=UTC).isoformat()
        status.state = EvolutionState.RUNNING
        status.decision = EvolutionDecision.CONTINUE
        status.error = ""
        status.finished_at = None
        status.terminal_reason = ""
        self._save(status, started, prior_elapsed)

        try:
            if not status.best_version_id:
                self._phase(status, "读取基础因子", 0.01, emit)
                base = self.services.load_base(config)
                self.services.check_candidate(config, base,
                                              timeout_seconds=self._remaining(config, started, prior_elapsed))
                self.store.save_version(research_id, base)
                baseline_exp = self.services.begin_experiment(config, base, "")
                self.store.append_event(
                    research_id, "baseline_started",
                    {"version_id": base.version_id, "experiment_id": baseline_exp},
                )
                result = self._evaluate_training(
                    config,
                    status,
                    base,
                    baseline_exp,
                    started,
                    prior_elapsed,
                    emit,
                    baseline=True,
                )
                status.best_version_id = base.version_id
                status.current_version_id = base.version_id
                self.store.append_event(
                    research_id,
                    "baseline_completed",
                    {"version_id": base.version_id, "result": result.model_dump(mode="json")},
                )
                self.store.append_memory(
                    research_id,
                    {
                        "iteration": 0,
                        "version_id": base.version_id,
                        "change": "基础版本",
                        "outcome": "baseline",
                        "diagnosis": "作为后续候选的固定比较基线",
                        "failure_reason": "",
                        "next_step": "仅根据训练指标提出可证伪的改进；验证结果在候选冻结前不可见",
                        "metrics": self._metric_digest(result),
                    },
                )
                self._save(status, started, prior_elapsed)

            best = self.store.load_version(research_id, status.best_version_id)
            best_result = self._latest_training_result(research_id, status.best_version_id)
            parent_exp = best_result.experiment_id
            baseline_id = next(event["payload"]["version_id"] for event in self.store.detail(research_id)["events"]
                               if event["event_type"] == "baseline_started")
            baseline = self.store.load_version(research_id, baseline_id)

            while status.iteration < config.max_iterations:
                self._guard(config, status, started, prior_elapsed)
                status.iteration += 1
                iteration = status.iteration
                self._phase(
                    status,
                    f"第 {iteration} 轮：生成候选",
                    self._progress(config, iteration, 0.05),
                    emit,
                )
                candidate: FactorVersion | None = None
                experiment_id = ""
                try:
                    self.store.append_event(
                        research_id,
                        "model_context_dispatched",
                        {
                            "iteration": iteration,
                            "task": "factor_candidate",
                            "visible_data": ["train", "session_memory"],
                            "locked_test_visible": False,
                        },
                    )
                    candidate = self.services.propose(
                        config,
                        best,
                        self.store.memories(research_id),
                        iteration=iteration,
                        timeout_seconds=self._remaining(config, started, prior_elapsed),
                    )
                    from .selection import definition_key
                    prior_versions = [FactorVersion.model_validate(item)
                                      for item in self.store.detail(research_id)['versions']]
                    if any(definition_key(candidate) == definition_key(prior) for prior in prior_versions):
                        raise QuantTrainingError(code='EVOLUTION_DUPLICATE_CANDIDATE',
                            message='该公式和处理链在本会话已评价，停止重复计算；更名和注释不构成新候选。',
                            blocking=False)
                    status.current_version_id = candidate.version_id
                    self._phase(
                        status,
                        f"第 {iteration} 轮：代码试运行",
                        self._progress(config, iteration, 0.18),
                        emit,
                    )
                    self.services.check_candidate(
                        config,
                        candidate,
                        timeout_seconds=self._remaining(config, started, prior_elapsed),
                    )
                    self.store.save_version(research_id, candidate)
                    experiment_id = self.services.begin_experiment(config, candidate, parent_exp)
                    self.store.append_event(
                        research_id,
                        "candidate_created",
                        {
                            "iteration": iteration,
                            "version_id": candidate.version_id,
                            "parent_version_id": candidate.parent_version_id,
                            "proposal_id": candidate.proposal_id,
                            "experiment_id": experiment_id,
                            "change_type": candidate.change_type,
                            "change_summary": candidate.change_summary,
                            "model_name": candidate.model_name,
                            "generation_attempts": candidate.generation_attempts,
                        },
                    )
                    result = self._evaluate_training(
                        config,
                        status,
                        candidate,
                        experiment_id,
                        started,
                        prior_elapsed,
                        emit,
                    )
                except _StopRequested:
                    raise
                except QuantTrainingError as exc:
                    if exc.code == 'EVOLUTION_ANALYST_STOP':
                        return self._finish(status, EvolutionDecision.STOPPED,
                                            '证据分析停止：' + exc.message, started, prior_elapsed, emit)
                    if exc.code == 'EVOLUTION_ANALYSIS_TIMEOUT':
                        raise
                    if exc.code == 'EVOLUTION_INPUT_QUALITY_INVALID' or exc.code.startswith(('RESEARCH_', 'BACKTEST_', 'ADS_')):
                        # A bad shared input cannot be repaired by trying another formula.
                        raise
                    status.failed_rounds += 1
                    status.no_improvement_count += 1
                    if experiment_id:
                        self.services.abort_experiment(
                            experiment_id, reason=exc.message, code=exc.code
                        )
                    self.store.append_event(
                        research_id,
                        "round_failed",
                        {
                            "iteration": iteration,
                            "version_id": candidate.version_id if candidate else "",
                            "code": exc.code,
                            "message": exc.message,
                            "model_name": str(exc.context.get("model_name") or ""),
                            "generation_attempts": exc.context.get("attempts"),
                        },
                    )
                    self.store.append_memory(
                        research_id,
                        {
                            "iteration": iteration,
                            "outcome": "failed",
                            "failure_code": exc.code,
                            "diagnosis": exc.message,
                            "next_step": "避开本轮失败路径并只改动一个维度。",
                        },
                    )
                    emit(f"第 {iteration} 轮失败，已完整留档：[{exc.code}] {exc.message}")
                    self._save(status, started, prior_elapsed)
                    if status.no_improvement_count >= config.no_improvement_rounds:
                        break
                    continue

                assert candidate is not None
                comparison_result = best_result
                improved = self._training_is_improved(result, best_result)
                self.services.record_result(experiment_id, result, improved=improved)
                status.successful_rounds += 1
                if improved:
                    best = candidate
                    best_result = result
                    parent_exp = experiment_id
                    status.best_version_id = candidate.version_id
                    status.no_improvement_count = 0
                else:
                    status.no_improvement_count += 1

                self._phase(
                    status,
                    f"第 {iteration} 轮：分析结果",
                    self._progress(config, iteration, 0.88),
                    emit,
                )
                self.store.append_event(
                    research_id,
                    "model_context_dispatched",
                    {
                        "iteration": iteration,
                        "task": "structured_config",
                        "visible_data": ["train"],
                        "locked_test_visible": False,
                    },
                )
                review = self.services.review(
                    config, candidate, result, comparison_result,
                    timeout_seconds=self._remaining(config, started, prior_elapsed),
                )
                self.services.record_review(experiment_id, review, improved=improved)
                self.store.append_memory(
                    research_id,
                    {
                        "iteration": iteration,
                        "version_id": candidate.version_id,
                        "change": candidate.change_summary,
                        "outcome": "improved" if improved else "not_improved",
                        "diagnosis": review.assessment,
                        "failure_reason": review.failure_reason,
                        "next_step": review.next_change,
                        "metrics": self._metric_digest(result),
                    },
                )
                self.store.append_event(
                    research_id,
                    "round_completed",
                    {
                        "iteration": iteration,
                        "version_id": candidate.version_id,
                        "improved": improved,
                        "constraints_passed": result.constraints_passed,
                        "review": review.model_dump(mode="json"),
                    },
                )
                self._save(status, started, prior_elapsed)

                if review.decision == "stop" or status.no_improvement_count >= config.no_improvement_rounds:
                    break

            return self._finalize_candidate(config, status, baseline, best, best_result,
                                            started, prior_elapsed, emit)
        except _StopRequested as exc:
            decision = (
                EvolutionDecision.CANCELLED
                if str(exc) == "cancelled"
                else EvolutionDecision.STOPPED
            )
            reason = (
                "收到取消请求" if decision is EvolutionDecision.CANCELLED else "达到最大运行时间"
            )
            return self._finish(status, decision, reason, started, prior_elapsed, emit)
        except QuantTrainingError as exc:
            status.state = EvolutionState.FAILED
            status.decision = EvolutionDecision.FAILED
            status.error = f"[{exc.code}] {exc.message}"
            status.terminal_reason = "结构化业务错误"
            status.finished_at = datetime.now(tz=UTC).isoformat()
            self._save(status, started, prior_elapsed)
            self.store.append_event(
                research_id,
                "session_failed",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "model_name": str(exc.context.get("model_name") or ""),
                },
            )
            self._summarize(status)
            self.store.write_report(research_id)
            raise
        except Exception as exc:
            wrapped = QuantTrainingError(
                code="EVOLUTION_UNEXPECTED_ERROR",
                message=f"自动进化遇到未预期错误：{exc}",
                context={"exception": type(exc).__name__},
                blocking=True,
                suggested_action=f"检查会话 {research_id} 的事件与任务日志后执行断点续跑。",
            )
            status.state = EvolutionState.FAILED
            status.decision = EvolutionDecision.FAILED
            status.error = f"[{wrapped.code}] {wrapped.message}"
            status.terminal_reason = "未预期错误"
            status.finished_at = datetime.now(tz=UTC).isoformat()
            self._save(status, started, prior_elapsed)
            self.store.append_event(
                research_id,
                "session_failed",
                {"code": wrapped.code, "message": wrapped.message},
            )
            self._summarize(status)
            self.store.write_report(research_id)
            raise wrapped from exc

    def _finalize_candidate(self, config, status, baseline, candidate, training_result,
                            started, prior_elapsed, emit):
        research_id = status.research_id
        if candidate.version_id == baseline.version_id:
            return self._finish(status, EvolutionDecision.STOPPED,
                                "训练搜索未获得优于基线的候选，未运行验证和锁定测试",
                                started, prior_elapsed, emit)
        # This exact window is included in the final aggregate, whose risk
        # measures take the worst fold. Later folds cannot repair this failure.
        train_metrics = training_result.train.metrics
        if (abs(float(train_metrics['risk']['max_drawdown'])) > config.targets.max_drawdown
                or float(train_metrics['trading']['annualized_turnover_oneside'])
                > config.targets.max_annualized_turnover):
            return self._finish(status, EvolutionDecision.STOPPED,
                                "最佳候选仍违反固定训练窗的回撤或换手约束，跳过验证和锁定测试",
                                started, prior_elapsed, emit)
        self.store.append_event(status.research_id, "finalist_frozen", {
            "version_id": candidate.version_id, "code_hash": candidate.code_hash,
            "selection_source": "training_only", "sent_to_model": False,
        })
        self._phase(status, "候选已冻结：评估基线与候选的滚动验证表现", .91, emit)
        baseline_training = self._latest_training_result(status.research_id, baseline.version_id)
        baseline_result = self._evaluate_version(config, status, baseline, baseline_training.experiment_id,
                                                started, prior_elapsed, emit, baseline=True)
        experiment_id = training_result.experiment_id
        result = self._evaluate_version(config, status, candidate, experiment_id,
                                        started, prior_elapsed, emit)
        improved = self._is_improved(result, baseline_result)
        self.services.record_result(experiment_id, result, improved=improved)
        self.store.append_event(status.research_id, "finalist_validation_completed", {
            "version_id": candidate.version_id, "improved": improved,
            "constraints_passed": result.constraints_passed, "sent_to_model": False,
        })
        if improved and result.constraints_passed:
            if not config.run_locked_test:
                return self._finish(status, EvolutionDecision.STOPPED,
                    '已得到通过开发期门槛的候选；本批锁定测试封存，待跨因子比较后固定唯一最终方案',
                    started, prior_elapsed, emit)
            self._phase(status, "运行锁定测试", 0.97, emit)
            exposure_id = self.locked_tests.reserve(
                base_factor=config.base_factor,
                research_id=research_id,
                version_id=candidate.version_id,
                code_hash=candidate.code_hash,
                window=config.test_window,
            )
            self.store.append_event(
                research_id,
                "locked_test_reserved",
                {
                    "exposure_id": exposure_id,
                    "version_id": candidate.version_id,
                    "window": config.test_window.model_dump(mode="json"),
                    "sent_to_model": False,
                },
            )
            try:
                test = self.services.evaluate(
                    config,
                    candidate,
                    window_name="test",
                    window=config.test_window,
                    timeout_seconds=self._remaining(config, started, prior_elapsed),
                    on_progress=emit,
                )
            except QuantTrainingError as exc:
                self.locked_tests.complete(exposure_id, event="failed", error_code=exc.code)
                self.store.append_event(
                    research_id,
                    "locked_test_failed",
                    {
                        "version_id": candidate.version_id,
                        "code": exc.code,
                        "message": exc.message,
                    },
                )
                raise
            except Exception as exc:
                self.locked_tests.complete(
                    exposure_id,
                    event="failed",
                    error_code=type(exc).__name__,
                )
                raise
            result.test = test
            status.locked_test_runs += 1
            self.services.record_result(experiment_id, result, improved=improved)
            test_passed, test_stability = self._test_passes(config, result)
            self.locked_tests.complete(
                exposure_id,
                event="completed",
                run_id=test.run_id,
                passed=test_passed,
            )
            self.services.record_locked_test(
                experiment_id,
                result,
                passed=test_passed,
                stability=test_stability,
            )
            self.store.append_event(
                research_id,
                "locked_test_completed",
                {
                    "version_id": candidate.version_id,
                    "run_id": test.run_id,
                    "passed": test_passed,
                    "generalization_classification": (
                        "locked_test_effective" if test_passed else "historical_overfit"
                    ),
                    "stability": test_stability,
                    "metrics": test.metrics,
                },
            )
            if test_passed:
                status.best_version_id = candidate.version_id
                return self._finish(
                    status,
                    EvolutionDecision.FROZEN,
                    "验证与锁定测试均达到目标",
                    started,
                    prior_elapsed,
                    emit,
                )
            return self._finish(
                status,
                EvolutionDecision.STOPPED,
                "锁定测试未达到约束，测试区间不再用于调参",
                started,
                prior_elapsed,
                emit,
            )

        return self._finish(status, EvolutionDecision.STOPPED,
                            "冻结候选未通过验证改进与准入门槛，结束搜索；锁定测试未使用",
                            started, prior_elapsed, emit)

    def _evaluate_training(self, config, status, version, experiment_id, started,
                           prior_elapsed, emit, *, baseline=False):
        self._guard(config, status, started, prior_elapsed)
        self.services.check_candidate(config, version,
                                      timeout_seconds=self._remaining(config, started, prior_elapsed))
        self._phase(status, "固定训练区间回测：" + version.version_id,
                    self._progress(config, max(1, status.iteration), .35), emit)
        train = self.services.evaluate(config, version, window_name="fixed_train",
                                       window=config.train_window,
                                       timeout_seconds=self._remaining(config, started, prior_elapsed),
                                       on_progress=emit)
        self._window_completed(status, experiment_id, version, train, "search", "train",
                               started, prior_elapsed)
        self._guard(config, status, started, prior_elapsed)
        result = VersionResult(version_id=version.version_id, experiment_id=experiment_id,
                               train=train, score=self._score(train.metrics))
        from .selection import training_quality_key
        if training_quality_key(result) is None:
            raise QuantTrainingError(code="BACKTEST_RESEARCH_METRICS_INVALID",
                                     message="训练结果缺少有限的实际收益、Calmar、回撤或换手指标。", blocking=True)
        self.store.append_event(status.research_id, "training_evaluated", result.model_dump(mode="json"))
        if baseline:
            self.services.record_result(experiment_id, result, improved=None)
        return result

    def _latest_training_result(self, research_id, version_id):
        for event in reversed(self.store.detail(research_id)["events"]):
            if event["event_type"] == "training_evaluated" and event["payload"]["version_id"] == version_id:
                return VersionResult.model_validate(event["payload"])
        raise QuantTrainingError(code="EVOLUTION_TRAINING_RESULT_MISSING",
                                 message=f"版本 {version_id} 缺少训练结果，不能继续搜索。", blocking=True)

    @staticmethod
    def _training_is_improved(current, best):
        from .selection import training_quality_key
        new, old = training_quality_key(current), training_quality_key(best)
        if new is not None and old is not None:
            from .selection import training_noninferiority
            if not training_noninferiority(current.train.metrics, best.train.metrics):
                return False
        return new is not None and (old is None or new > old)

    def _evaluate_version(
        self,
        config: EvolutionConfig,
        status,
        version: FactorVersion,
        experiment_id: str,
        started: float,
        prior_elapsed: float,
        emit: ProgressFn,
        *,
        baseline: bool = False,
    ) -> VersionResult:
        prefix = "基线" if baseline else f"第 {status.iteration} 轮"
        folds = build_folds(config)
        fold_results: list[FoldResult] = []
        progress_start = max(status.progress, 0.91)
        progress_end = min(0.97, progress_start + 0.025)
        windows = [(f"{fold.fold_id}_{kind}", window)
                   for fold in folds
                   for kind, window in (("train", fold.train_window), ("validation", fold.validation_window))]
        window_positions = {name: index for index, (name, _) in enumerate(windows)}

        def window_started(name):
            index = window_positions[name]
            fold_id, kind = name.rsplit("_", 1)
            label = "训练" if kind == "train" else "验证"
            progress = progress_start + (progress_end - progress_start) * index / len(windows)
            self._phase(status, f"{prefix}：{fold_id} ({index // 2 + 1}/{len(folds)}) {label}回测", progress, emit)

        def window_completed(name, result):
            fold_id, kind = name.rsplit("_", 1)
            status.progress = progress_start + (progress_end - progress_start) * (window_positions[name] + 1) / len(windows)
            self._window_completed(status, experiment_id, version, result, fold_id, kind, started, prior_elapsed)

        self._guard(config, status, started, prior_elapsed)
        if callable(getattr(type(self.services), "evaluate_windows", None)):
            results = self.services.evaluate_windows(
                config, version, windows=windows, research_id=status.research_id,
                timeout_seconds=self._remaining(config, started, prior_elapsed),
                on_progress=emit, on_window_started=window_started, on_window_completed=window_completed,
            )
            self._guard(config, status, started, prior_elapsed)
            if set(results) != set(window_positions):
                raise QuantTrainingError(code="EVOLUTION_WINDOWS_INCOMPLETE", message="训练或验证窗口结果不完整。", blocking=True)
        else:
            results = {}
            for name, window in windows:
                window_started(name)
                result = self.services.evaluate(
                    config, version, window_name=name, window=window,
                    timeout_seconds=self._remaining(config, started, prior_elapsed), on_progress=emit,
                )
                results[name] = result
                window_completed(name, result)
                self._guard(config, status, started, prior_elapsed)
        fold_results = [FoldResult(
            fold_id=fold.fold_id, train_window=fold.train_window, validation_window=fold.validation_window,
            train=results[f"{fold.fold_id}_train"], validation=results[f"{fold.fold_id}_validation"],
        ) for fold in folds]

        status.progress = progress_end
        self._save(status, started, prior_elapsed)

        train = aggregate_results([fold.train for fold in fold_results], label="train_aggregate")
        validation = aggregate_results(
            [fold.validation for fold in fold_results], label="validation_aggregate"
        )
        generalization = assess_generalization(config, fold_results)
        stability = self._stability(train.metrics, validation.metrics)
        result = VersionResult(
            version_id=version.version_id,
            experiment_id=experiment_id,
            train=train,
            validation=validation,
            score=generalization.recency_weighted_validation_excess_return,
            stability=stability,
            folds=fold_results,
            generalization=generalization,
            constraints_passed=self._constraints_pass(
                config,
                train.metrics,
                validation.metrics,
                stability,
                generalization.classification,
            ),
        )
        self.store.append_event(
            status.research_id,
            "version_evaluated",
            result.model_dump(mode="json"),
        )
        if baseline:
            self.services.record_result(experiment_id, result, improved=None)
        return result

    def _window_completed(self, status, experiment_id, version, result, fold_id, window_name, started, prior_elapsed):
        self.store.append_event(status.research_id, "window_completed", {
            "experiment_id": experiment_id, "version_id": version.version_id,
            "fold_id": fold_id, "window_name": window_name,
            "run_id": result.run_id, "metrics": result.metrics,
        })
        record_window = getattr(self.services, "record_window_result", None)
        if record_window:
            record_window(experiment_id, result, fold_id=fold_id, window_name=window_name)
        from quantlens.research_preflight import assess_input_quality

        quality = assess_input_quality(result.metrics)
        if not quality['passed']:
            raise QuantTrainingError(code='EVOLUTION_INPUT_QUALITY_INVALID',
                message='研究窗口的数据质量检查未通过：' + '；'.join(quality.get('issues') or []),
                context={'run_id': result.run_id, 'quality': quality}, blocking=True,
                suggested_action='删除受影响回测和实验，修复输入后新建研究；停止后续窗口和调参。')
        self._save(status, started, prior_elapsed)

    def _latest_result(self, research_id: str, version_id: str) -> VersionResult:
        detail = self.store.detail(research_id)
        for event in reversed(detail["events"]):
            if (
                event["event_type"] == "version_evaluated"
                and event["payload"].get("version_id") == version_id
            ):
                return VersionResult.model_validate(event["payload"])
        raise QuantTrainingError(
            code="EVOLUTION_RESULT_MISSING",
            message=f"版本 {version_id} 缺少评价结果，无法续跑。",
            blocking=True,
            suggested_action="检查会话 events.jsonl 是否完整。",
        )

    @staticmethod
    def _score(metrics: dict) -> float:
        return float((metrics.get("returns") or {}).get("annualized_excess_return") or 0)

    @classmethod
    def _stability(cls, train: dict, validation: dict) -> float:
        train_score = cls._score(train)
        valid_score = cls._score(validation)
        if train_score <= 0 or valid_score <= 0:
            return 0.0
        return min(valid_score / train_score, train_score / valid_score, 1.0)

    @classmethod
    def _constraints_pass(
        cls,
        config: EvolutionConfig,
        train: dict,
        validation: dict,
        stability: float,
        classification: str,
    ) -> bool:
        target = config.targets
        for metrics in (train, validation):
            if not metrics.get('input_quality', {}).get('passed', False):
                return False
            returns = metrics.get("returns") or {}
            risk = metrics.get("risk") or {}
            trading = metrics.get("trading") or {}
            if float(returns.get("annualized_return") or 0) <= 0:
                return False
            if (
                float(returns.get("annualized_excess_return") or 0)
                < target.min_annualized_excess_return
            ):
                return False
            if abs(float(risk.get("max_drawdown") or 0)) > target.max_drawdown:
                return False
            if (
                float(trading.get("annualized_turnover_oneside") or 0)
                > target.max_annualized_turnover
            ):
                return False
        return stability >= target.min_oos_stability and classification == "validation_generalized"

    @classmethod
    def _test_passes(cls, config: EvolutionConfig, result: VersionResult) -> tuple[bool, float]:
        assert result.validation is not None and result.test is not None
        metrics = result.test.metrics
        target = config.targets
        stability = cls._stability(result.validation.metrics, metrics)
        passed = (
            metrics.get('input_quality', {}).get('passed', False)
            and float((metrics.get('returns') or {}).get('annualized_return') or 0) > 0
            and cls._score(metrics) >= target.min_annualized_excess_return
            and abs(float((metrics.get("risk") or {}).get("max_drawdown") or 0))
            <= target.max_drawdown
            and float((metrics.get("trading") or {}).get("annualized_turnover_oneside") or 0)
            <= target.max_annualized_turnover
            and stability >= target.min_oos_stability
        )
        return passed, stability

    @staticmethod
    def _is_improved(result: VersionResult, best: VersionResult) -> bool:
        from .selection import quality_key
        candidate_key, parent_key = quality_key(result), quality_key(best)
        return candidate_key is not None and (parent_key is None or candidate_key > parent_key)

    @staticmethod
    def _metric_digest(result: VersionResult) -> dict:
        def one(window):
            if window is None:
                return {}
            metrics = window.metrics
            return {
                "run_id": window.run_id,
                "annualized_excess_return": (metrics.get("returns") or {}).get(
                    "annualized_excess_return"
                ),
                "annualized_return": (metrics.get("returns") or {}).get("annualized_return"),
                "calmar": (metrics.get("objective") or {}).get("value"),
                "max_drawdown": (metrics.get("risk") or {}).get("max_drawdown"),
                "annualized_turnover_oneside": (metrics.get("trading") or {}).get(
                    "annualized_turnover_oneside"
                ),
            }

        return {
            "train": one(result.train),
            "validation": one(result.validation),
            "stability": result.stability,
            "generalization": (
                result.generalization.model_dump(mode="json") if result.generalization else None
            ),
        }

    def _phase(self, status, phase: str, progress: float, emit: ProgressFn) -> None:
        status.phase = phase
        status.progress = min(max(progress, 0), 0.99)
        self.store.save_status(status)
        emit(phase)

    @staticmethod
    def _progress(config: EvolutionConfig, iteration: int, within: float) -> float:
        return min(0.95, 0.05 + ((iteration - 1 + within) / config.max_iterations) * 0.9)

    def _guard(self, config, status, started: float, prior_elapsed: float) -> None:
        latest = self.store.load_status(status.research_id)
        if latest.cancel_requested:
            status.cancel_requested = True
            raise _StopRequested("cancelled")
        if prior_elapsed + (time.monotonic() - started) >= config.max_runtime_seconds:
            raise _StopRequested("timeout")

    @staticmethod
    def _remaining(config: EvolutionConfig, started: float, prior_elapsed: float) -> float:
        remaining = config.max_runtime_seconds - prior_elapsed - (time.monotonic() - started)
        if remaining <= 0:
            raise _StopRequested("timeout")
        return remaining

    def _save(self, status, started: float, prior_elapsed: float) -> None:
        status.elapsed_seconds = round(prior_elapsed + (time.monotonic() - started), 3)
        self.store.save_status(status)

    def _finish(self, status, decision, reason, started, prior_elapsed, emit) -> dict:
        status.decision = decision
        status.state = (
            EvolutionState.CANCELLED
            if decision is EvolutionDecision.CANCELLED
            else EvolutionState.COMPLETED
        )
        status.phase = "已结束"
        status.progress = 1
        status.terminal_reason = reason
        status.finished_at = datetime.now(tz=UTC).isoformat()
        self._save(status, started, prior_elapsed)
        self.store.append_event(
            status.research_id,
            "session_finished",
            {
                "decision": decision.value,
                "reason": reason,
                "best_version_id": status.best_version_id,
            },
        )
        self._summarize(status)
        self.store.write_report(status.research_id)
        emit(f"自动进化结束：{decision.value}；{reason}")
        return self.store.detail(status.research_id)

    def _summarize(self, status):
        summarize = getattr(self.services, 'summarize_experiment', None)
        if summarize is None or status.state == EvolutionState.CANCELLED:
            return
        # Only terminal facts are sent. Validation/test metrics and the generated
        # sentence never enter proposal memory or candidate selection.
        try:
            config = self.store.load_config(status.research_id)
            events = self.store.detail(status.research_id)['events']
            types = {event['event_type'] for event in events}
            record = summarize(config, {'kind': 'factor', 'state': status.state.value,
                'decision': status.decision.value, 'reason': status.terminal_reason,
                'error': status.error, 'iterations': status.iteration,
                'training_completed': 'training_evaluated' in types,
                'validation_completed': 'finalist_validation_completed' in types,
                'locked_test_completed': 'locked_test_completed' in types})
            self.store.append_event(status.research_id, 'experiment_summary', record)
        except Exception as exc:
            # A presentation failure must not turn successful computation into failure.
            self.store.append_event(status.research_id, 'experiment_summary_failed', {'error': str(exc)})

__all__ = ["EvolutionEngine", "EvolutionServices"]
