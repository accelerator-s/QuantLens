from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from exceptions import QuantTrainingError
from lifecycle.models import ObjectType, RunPurpose, StrategyVersion

from .models import StrategyCandidateResult, StrategyEvolutionConfig
from .services import StrategyEvolutionServices, _metric

def new_program_id(profile_name: str) -> str:
    now = datetime.now(tz=UTC)
    digest = hashlib.sha256(
        f"{profile_name}:{now.isoformat()}".encode("utf-8")
    ).hexdigest()[:8].upper()
    return f"RP-STR-{now:%Y%m%dT%H%M%S}-{digest}"

class StrategyEvolutionEngine:
    def __init__(self, services: StrategyEvolutionServices | None = None) -> None:
        self.services = services or StrategyEvolutionServices()

    def run(
        self,
        config: StrategyEvolutionConfig,
        *,
        program_id: str,
        on_progress=None,
    ) -> dict[str, Any]:
        emit = on_progress or (lambda _message: None)
        deadline = monotonic() + config.max_runtime_seconds

        def ensure_runtime_budget(stage: str) -> None:
            if monotonic() >= deadline:
                raise QuantTrainingError(
                    code="STRATEGY_EVOLUTION_RUNTIME_BUDGET_EXHAUSTED",
                    message=f"多因子研究在 {stage} 前用尽本次运行时间预算。",
                    context={"max_runtime_seconds": config.max_runtime_seconds},
                    blocking=True,
                    suggested_action="核对已完成 artifact 后显式恢复；不得改动原研究切分和锁定测试。",
                )
        program = self.services.store.get(ObjectType.RESEARCH_PROGRAM, program_id)
        program_state = self.services.store.current_state(
            ObjectType.RESEARCH_PROGRAM, program_id
        )
        if program_state == "paused":
            self.services.transition(
                ObjectType.RESEARCH_PROGRAM,
                program_id,
                "queued",
                "用户显式恢复，按原配置检查完整 artifact",
            )
        elif program_state not in {"queued", "running"}:
            raise QuantTrainingError(
                code="STRATEGY_EVOLUTION_NOT_RESUMABLE",
                message=f"策略研究 {program_id} 处于 {program_state}，不允许启动或恢复。",
                context={"program_id": program_id, "state": program_state},
                blocking=True,
                suggested_action="只能恢复 paused 研究；已冻结、完成或失败的任务必须保持终态。",
            )
        self.services.transition(
            ObjectType.RESEARCH_PROGRAM, program_id, "running", "多因子 AI 研究开始"
        )
        history: list[dict[str, Any]] = []
        all_results: dict[str, StrategyCandidateResult] = {}
        evaluating: set[str] = set()
        try:
            restored = self._restore_checkpoint(config, program, emit=emit)
            if restored is None:
                baseline = self.services.baseline(config, program)
                self.services.transition(
                    ObjectType.STRATEGY_VERSION,
                    baseline.strategy_version_id,
                    "evaluating",
                    "评价冻结的多因子基线",
                )
                evaluating.add(baseline.strategy_version_id)
                emit(f"评价基线策略 {baseline.strategy_version_id}")
                best = baseline
                best_result = self.services.evaluate(config, baseline, emit=emit)
                all_results[baseline.strategy_version_id] = best_result
                history.append(self._history(baseline, best_result, None, "baseline"))
                no_improvement = 0
                completed_iterations = 0
            else:
                (
                    best,
                    best_result,
                    history,
                    all_results,
                    no_improvement,
                    completed_iterations,
                    evaluating,
                ) = restored
                emit(
                    f"已从不可变 artifact 恢复 {len(all_results)} 个策略评价，"
                    f"从第 {completed_iterations + 1} 轮继续"
                )

            for iteration in range(completed_iterations + 1, config.max_iterations + 1):
                ensure_runtime_budget(f"第 {iteration} 轮")
                emit(f"第 {iteration} 轮：AI 从父版本提出单维度改动")
                candidate = None
                try:
                    candidate = self.services.propose(
                        config, best, history, iteration=iteration
                    )
                    self.services.transition(
                        ObjectType.STRATEGY_VERSION,
                        candidate.strategy_version_id,
                        "evaluating",
                        "schema 与单维变更检查通过",
                    )
                    evaluating.add(candidate.strategy_version_id)
                    result = self.services.evaluate(config, candidate, emit=emit)
                    ablation, ablation_runs = self.services.ablation(
                        config, candidate, emit=emit
                    )
                    if ablation is not None:
                        result.ablation_strategy_version_id = ablation.strategy_version_id
                        result.ablation_run_ids = [item.run_id for item in ablation_runs]
                        result.ablation_delta = self.services.ablation_delta(
                            result, ablation_runs
                        )
                    review = self.services.review(config, candidate, result, best_result)
                except QuantTrainingError as exc:
                    if candidate is not None and candidate.strategy_version_id in evaluating:
                        self.services.transition(
                            ObjectType.STRATEGY_VERSION,
                            candidate.strategy_version_id,
                            "rejected",
                            f"本轮显式失败：[{exc.code}] {exc.message}",
                        )
                        evaluating.discard(candidate.strategy_version_id)
                    no_improvement += 1
                    history.append(
                        {
                            "iteration": iteration,
                            "outcome": "failed",
                            "failure_code": exc.code,
                            "reason": exc.message,
                        }
                    )
                    emit(f"第 {iteration} 轮失败，已显式留档：[{exc.code}] {exc.message}")
                    if no_improvement >= config.no_improvement_rounds:
                        break
                    continue

                all_results[candidate.strategy_version_id] = result
                improved = self._rank(result) > self._rank(best_result)
                history.append(
                    self._history(candidate, result, best_result, review.get("assessment", ""))
                )
                if improved:
                    if best.strategy_version_id in evaluating:
                        self.services.transition(
                            ObjectType.STRATEGY_VERSION,
                            best.strategy_version_id,
                            "rejected",
                            "被更优样本外候选取代",
                        )
                        evaluating.discard(best.strategy_version_id)
                    best, best_result = candidate, result
                    no_improvement = 0
                    emit(
                        f"第 {iteration} 轮改善：分数 {result.score:.4f}，"
                        f"因子剔除增量 {result.ablation_delta}"
                    )
                else:
                    self.services.transition(
                        ObjectType.STRATEGY_VERSION,
                        candidate.strategy_version_id,
                        "rejected",
                        "未超过当前父版本的样本外综合排名",
                    )
                    evaluating.discard(candidate.strategy_version_id)
                    no_improvement += 1

                if improved and result.constraints_passed and review.get("decision") == "freeze":
                    break
                if review.get("decision") == "stop":
                    break
                if no_improvement >= config.no_improvement_rounds:
                    break

            if not best_result.constraints_passed:
                self.services.transition(
                    ObjectType.STRATEGY_VERSION,
                    best.strategy_version_id,
                    "rejected",
                    "walk-forward 综合约束未通过",
                )
                evaluating.discard(best.strategy_version_id)
                result_payload = self._result(
                    program_id, None, False, history, all_results
                )
                from lifecycle.reporting import ResearchReportService

                report = ResearchReportService(self.services.store).generate(program_id)
                result_payload["research_report_id"] = report.research_report_id
                self.services.transition(
                    ObjectType.RESEARCH_PROGRAM,
                    program_id,
                    "completed",
                    "没有候选获得锁定测试资格，研究报告已冻结",
                )
                return result_payload

            ensure_runtime_budget("锁定测试")
            emit("最佳候选已冻结，现在且仅现在开封锁定测试")
            self.services.transition(
                ObjectType.STRATEGY_VERSION,
                best.strategy_version_id,
                "frozen",
                "训练与 walk-forward 候选定义已冻结",
            )
            evaluating.discard(best.strategy_version_id)
            self.services.transition(
                ObjectType.RESEARCH_PROGRAM,
                program_id,
                "frozen",
                "模型后续不再可见候选参数调整通道",
            )
            locked = self.services.run_window(
                config,
                best,
                config.test_window,
                RunPurpose.LOCKED_TEST,
                emit=emit,
            )
            passed = self._locked_passes(config, locked.metrics)
            exposure = self.services.locked_exposure(best, locked, passed=passed)
            if passed:
                self.services.transition(
                    ObjectType.STRATEGY_VERSION,
                    best.strategy_version_id,
                    "challenger",
                    "唯一锁定测试通过，进入影子竞争",
                )
            else:
                self.services.transition(
                    ObjectType.STRATEGY_VERSION,
                    best.strategy_version_id,
                    "rejected",
                    "锁定测试未达标，该区间禁止继续调参",
                )
            result_payload = self._result(
                program_id, best, passed, history, all_results
            )
            result_payload.update(
                {
                    "locked_test_run_id": locked.run_id,
                    "locked_test_exposure_id": exposure.exposure_id,
                    "locked_test_passed": passed,
                }
            )
            from lifecycle.reporting import ResearchReportService

            report = ResearchReportService(self.services.store).generate(program_id)
            result_payload["research_report_id"] = report.research_report_id
            self.services.transition(
                ObjectType.RESEARCH_PROGRAM,
                program_id,
                "completed",
                "策略研究、唯一锁定测试和数值引用报告已完成",
            )
            return result_payload
        except Exception as exc:
            failure_code = getattr(exc, "code", type(exc).__name__)
            if failure_code != "STRATEGY_EVOLUTION_RUNTIME_BUDGET_EXHAUSTED":
                for strategy_id in list(evaluating):
                    try:
                        self.services.transition(
                            ObjectType.STRATEGY_VERSION,
                            strategy_id,
                            "rejected",
                            "研究任务异常终止",
                        )
                    except QuantTrainingError as cleanup_exc:
                        emit(
                            f"清理策略版本 {strategy_id} 时保留原错误："
                            f"[{cleanup_exc.code}] {cleanup_exc.message}"
                        )
            current = self.services.store.current_state(
                ObjectType.RESEARCH_PROGRAM, program_id
            )
            if current in {"running", "frozen"}:
                if (
                    current == "running"
                    and failure_code
                    == "STRATEGY_EVOLUTION_RUNTIME_BUDGET_EXHAUSTED"
                ):
                    self.services.transition(
                        ObjectType.RESEARCH_PROGRAM,
                        program_id,
                        "paused",
                        "本次运行时间预算用尽，保留完整 artifact 等待显式恢复",
                        payload={"failure_code": failure_code},
                    )
                else:
                    self.services.transition(
                        ObjectType.RESEARCH_PROGRAM,
                        program_id,
                        "failed",
                        str(exc),
                        payload={"failure_code": failure_code},
                    )
            if isinstance(exc, QuantTrainingError):
                raise
            raise QuantTrainingError(
                code="STRATEGY_EVOLUTION_UNEXPECTED_FAILURE",
                message=f"多因子策略研究异常终止：{exc}",
                context={"program_id": program_id, "exception": type(exc).__name__},
                blocking=True,
                suggested_action="核对最后完成的不可变版本和回测 artifact 后恢复新任务。",
            ) from exc

    def _restore_checkpoint(
        self,
        config: StrategyEvolutionConfig,
        program,
        *,
        emit,
    ) -> tuple[
        StrategyVersion,
        StrategyCandidateResult,
        list[dict[str, Any]],
        dict[str, StrategyCandidateResult],
        int,
        int,
        set[str],
    ] | None:
        
        records: list[StrategyVersion] = []
        for row in self.services.store.list_objects(
            ObjectType.STRATEGY_VERSION, limit=5000
        ):
            record = self.services.store.get(
                ObjectType.STRATEGY_VERSION, row["object_id"]
            )
            assert isinstance(record, StrategyVersion)
            if (
                record.research_program_id == program.program_id
                and not record.change_summary.startswith("剔除最低权重因子")
            ):
                records.append(record)
        if not records:
            return None
        records.sort(key=lambda item: item.created_at)
        results: dict[str, StrategyCandidateResult] = {}
        history: list[dict[str, Any]] = []
        evaluating: set[str] = set()
        best: StrategyVersion | None = None
        best_result: StrategyCandidateResult | None = None
        no_improvement = 0

        for index, candidate in enumerate(records):
            state = self.services.store.current_state(
                ObjectType.STRATEGY_VERSION, candidate.strategy_version_id
            )
            if state == "proposed":
                self.services.transition(
                    ObjectType.STRATEGY_VERSION,
                    candidate.strategy_version_id,
                    "evaluating",
                    "恢复时继续评价已冻结候选定义",
                )
                state = "evaluating"
            if state == "evaluating":
                evaluating.add(candidate.strategy_version_id)
            elif state == "rejected" and not self.services.evaluation_complete(
                config, candidate
            ):
                history.append(
                    {
                        "iteration": index,
                        "strategy_version_id": candidate.strategy_version_id,
                        "outcome": "failed",
                        "reason": "上次任务已明确拒绝且评价不完整，不复用半成品",
                    }
                )
                no_improvement += 1
                continue
            elif state not in {"evaluating", "rejected"}:
                raise QuantTrainingError(
                    code="STRATEGY_RESUME_VERSION_STATE_INVALID",
                    message=(
                        f"恢复时发现策略版本 {candidate.strategy_version_id} "
                        f"已处于 {state}。"
                    ),
                    blocking=True,
                    suggested_action="已冻结或进入 challenger 的研究不得回到调参阶段。",
                )

            emit(f"恢复核验策略 {candidate.strategy_version_id}")
            result = self.services.evaluate(config, candidate, emit=emit)
            if index > 0:
                ablation, ablation_runs = self.services.ablation(
                    config, candidate, emit=emit
                )
                if ablation is not None:
                    result.ablation_strategy_version_id = ablation.strategy_version_id
                    result.ablation_run_ids = [item.run_id for item in ablation_runs]
                    result.ablation_delta = self.services.ablation_delta(
                        result, ablation_runs
                    )
            results[candidate.strategy_version_id] = result
            previous = best_result
            improved = best_result is None or self._rank(result) > self._rank(best_result)
            if improved:
                best, best_result = candidate, result
                no_improvement = 0
            else:
                no_improvement += 1
            history.append(
                self._history(
                    candidate,
                    result,
                    previous,
                    "baseline" if index == 0 else "从完整 artifact 恢复",
                )
            )

        if best is None or best_result is None:
            raise QuantTrainingError(
                code="STRATEGY_RESUME_NO_COMPLETE_BASELINE",
                message="恢复点中没有可核验的完整基线评价。",
                blocking=True,
                suggested_action="检查基线回测 artifact；不要手工修改生命周期账本。",
            )
        for strategy_id in list(evaluating):
            if strategy_id == best.strategy_version_id:
                continue
            self.services.transition(
                ObjectType.STRATEGY_VERSION,
                strategy_id,
                "rejected",
                "恢复后根据已冻结样本外指标重建排名",
            )
            evaluating.discard(strategy_id)
        best_state = self.services.store.current_state(
            ObjectType.STRATEGY_VERSION, best.strategy_version_id
        )
        if best_state != "evaluating":
            raise QuantTrainingError(
                code="STRATEGY_RESUME_BEST_VERSION_TERMINAL",
                message=f"恢复后最优版本处于 {best_state}，不能继续调参。",
                blocking=True,
                suggested_action="保持终态并另建不使用已曝光测试区间的研究。",
            )
        evaluating.add(best.strategy_version_id)
        return (
            best,
            best_result,
            history,
            results,
            no_improvement,
            max(0, len(records) - 1),
            evaluating,
        )

    @staticmethod
    def _rank(result: StrategyCandidateResult) -> tuple[int, float]:
        return (1 if result.constraints_passed else 0, result.score)

    @staticmethod
    def _locked_passes(config: StrategyEvolutionConfig, metrics: dict) -> bool:
        target = config.targets
        return (
            _metric(metrics, "returns", "annualized_excess_return")
            >= target.min_annualized_excess_return
            and _metric(metrics, "risk", "sharpe") >= target.min_sharpe
            and abs(_metric(metrics, "risk", "max_drawdown")) <= target.max_drawdown
            and _metric(metrics, "trading", "annualized_turnover_oneside")
            <= target.max_annualized_turnover
        )

    @staticmethod
    def _history(
        strategy: StrategyVersion,
        result: StrategyCandidateResult,
        parent: StrategyCandidateResult | None,
        assessment: str,
    ) -> dict[str, Any]:
        return {
            "strategy_version_id": strategy.strategy_version_id,
            "change_dimension": strategy.change_dimension.value,
            "change_summary": strategy.change_summary,
            "constraints_passed": result.constraints_passed,
            "score": result.score,
            "score_delta": result.score - parent.score if parent else 0,
            "aggregate_metrics": result.aggregate_metrics,
            "ablation_delta": result.ablation_delta,
            "assessment": assessment,
        }

    @staticmethod
    def _result(
        program_id: str,
        best: StrategyVersion | None,
        passed: bool,
        history: list[dict[str, Any]],
        results: dict[str, StrategyCandidateResult],
    ) -> dict[str, Any]:
        return {
            "program_id": program_id,
            "best_strategy_version_id": best.strategy_version_id if best else "",
            "challenger_created": passed,
            "history": history,
            "results": {
                key: value.model_dump(mode="json") for key, value in results.items()
            },
        }

__all__ = ["StrategyEvolutionEngine", "new_program_id"]
