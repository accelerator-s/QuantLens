from __future__ import annotations

import hashlib
import json
from statistics import mean
from typing import Any

from exceptions import LifecycleError

from .models import (
    ChampionDecision,
    DecisionAction,
    EvaluationRun,
    ObjectType,
    RunPurpose,
    StrategyVersion,
    TimeWindow,
)
from .governance import load_champion_policy
from .states import ActorType
from .store import LifecycleStore

def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class ChampionService:
    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    def slots(self) -> list[dict[str, Any]]:
        slots: dict[str, dict[str, Any]] = {}
        for row in self.store.list_objects(ObjectType.STRATEGY_VERSION, limit=5000):
            strategy = self.store.get(ObjectType.STRATEGY_VERSION, row["object_id"])
            assert isinstance(strategy, StrategyVersion)
            slot = slots.setdefault(
                strategy.strategy_name,
                {"strategy_name": strategy.strategy_name, "champion": None, "challengers": [], "watching": []},
            )
            item = {**row, "strategy_version": strategy}
            if row["state"] == "champion":
                slot["champion"] = item
            elif row["state"] == "challenger":
                slot["challengers"].append(item)
            elif row["state"] == "watching":
                slot["watching"].append(item)
        return sorted(slots.values(), key=lambda item: item["strategy_name"])

    def bootstrap(self, strategy_id: str, *, reason: str, decided_by: str) -> None:
        strategy = self._strategy(strategy_id)
        current = self.store.current_state(ObjectType.STRATEGY_VERSION, strategy_id)
        if current == "champion" and self._champion(strategy.strategy_name) == strategy:
            return
        if current != "challenger":
            raise LifecycleError(
                code="CHAMPION_BOOTSTRAP_NOT_CHALLENGER",
                message=f"策略版本 {strategy_id} 不是 challenger。",
                blocking=True,
                suggested_action="先完成锁定测试和影子观察。",
            )
        if self._champion(strategy.strategy_name) is not None:
            raise LifecycleError(
                code="CHAMPION_BOOTSTRAP_SLOT_OCCUPIED",
                message=f"策略槽位 {strategy.strategy_name} 已有 champion。",
                blocking=True,
                suggested_action="创建 ChampionDecision 证据包进行竞争晋升。",
            )
        shadow_runs = self._runs(strategy_id, RunPurpose.SHADOW)
        required = load_champion_policy().min_shadow_observations
        if len(shadow_runs) < required:
            raise LifecycleError(
                code="CHAMPION_BOOTSTRAP_SHADOW_NOT_MATURED",
                message=(
                    f"策略版本 {strategy_id} 只有 {len(shadow_runs)} 个到期影子观察，"
                    f"治理配置要求至少 {required} 个。"
                ),
                blocking=True,
                suggested_action="继续每日运行，达到最小影子观察数后再人工建立 champion。",
            )
        for run in shadow_runs[-required:]:
            self._run_metrics(run)
        self.store.transition(
            ObjectType.STRATEGY_VERSION,
            strategy_id,
            "champion",
            actor_type=ActorType.HUMAN,
            actor_ref=decided_by,
            reason=reason,
        )

    def propose(self, challenger_id: str) -> ChampionDecision:
        challenger = self._strategy(challenger_id)
        champion = self._champion(challenger.strategy_name)
        if champion is None:
            raise LifecycleError(
                code="CHAMPION_DECISION_BOOTSTRAP_FIRST",
                message=f"策略槽位 {challenger.strategy_name} 尚无 champion。",
                blocking=True,
                suggested_action="影子观察通过后使用人工 bootstrap。",
            )
        locked = self._runs(challenger_id, RunPurpose.LOCKED_TEST)
        challenger_shadow = self._runs(challenger_id, RunPurpose.SHADOW)
        champion_shadow = self._runs(champion.strategy_version_id, RunPurpose.SHADOW)
        pairs = self._matching_pairs(champion_shadow, challenger_shadow)
        policy = load_champion_policy()
        if not locked or len(pairs) < policy.min_shadow_observations:
            raise LifecycleError(
                code="CHAMPION_DECISION_EVIDENCE_NOT_READY",
                message=(
                    "挑战者缺少锁定测试，或同快照同区间影子观察不足："
                    f"{len(pairs)}/{policy.min_shadow_observations}。"
                ),
                blocking=True,
                suggested_action="继续每日运行并等待同期影子标签到期。",
            )
        selected = pairs[-policy.min_shadow_observations :]
        comparisons = [self._shadow_delta(left, right) for left, right in selected]
        net_delta = mean(item["net_return_delta"] for item in comparisons)
        cost_delta = mean(item["cost_rate_delta"] for item in comparisons)
        if (
            net_delta < policy.min_challenger_net_excess_return
            or cost_delta > policy.max_cost_rate_increase
            or mean(item["drawdown_worsening"] for item in comparisons)
            > policy.max_drawdown_worsening
            or mean(item["turnover_delta"] for item in comparisons)
            > policy.max_turnover_increase
        ):
            drawdown_delta = mean(
                item["drawdown_worsening"] for item in comparisons
            )
            turnover_delta = mean(item["turnover_delta"] for item in comparisons)
            raise LifecycleError(
                code="CHAMPION_DECISION_THRESHOLD_NOT_MET",
                message=(
                    f"挑战者同期净收益增量 {net_delta:.4%}、成本率增量 {cost_delta:.4%}，"
                    f"回撤恶化 {drawdown_delta:.4%}、换手增量 {turnover_delta:.4f}，"
                    "未满足治理阈值。"
                ),
                context={
                    "net_return_delta": net_delta,
                    "cost_rate_delta": cost_delta,
                    "drawdown_worsening": drawdown_delta,
                    "turnover_delta": turnover_delta,
                    "policy": policy.model_dump(mode="json"),
                },
                blocking=True,
                suggested_action="保留 challenger 继续观察，或由新研究版本解决收益/成本问题；不得降低阈值后偷看同一结果。",
            )
        evidence = [locked[-1].evaluation_run_id]
        drawdown_delta = mean(item["drawdown_worsening"] for item in comparisons)
        turnover_delta = mean(item["turnover_delta"] for item in comparisons)
        for left, right in selected:
            evidence.extend([left.evaluation_run_id, right.evaluation_run_id])
        evidence = list(dict.fromkeys(evidence))
        decision_id = f"CD-{_hash([champion.strategy_version_id, challenger_id, evidence])[:20].upper()}"
        shadow_window = TimeWindow(
            start=min(left.window.start for left, _right in selected),
            end=max(left.window.end for left, _right in selected),
        )
        record = ChampionDecision(
            decision_id=decision_id,
            champion_strategy_version_id=champion.strategy_version_id,
            challenger_strategy_version_id=challenger_id,
            action=DecisionAction.PROMOTE,
            evidence_run_ids=evidence,
            shadow_window=shadow_window,
            rationale=(
                f"挑战者锁定测试完成；{len(selected)} 期同期影子平均净收益增量 "
                f"{net_delta:.4%}，平均成本率增量 {cost_delta:.4%}，"
                f"回撤恶化 {drawdown_delta:.4%}，换手增量 {turnover_delta:.4f}，"
                "通过治理阈值。"
            ),
            risk_delta={
                "shadow_observations": len(selected),
                "mean_net_return_delta": net_delta,
                "mean_cost_rate_delta": cost_delta,
                "mean_drawdown_worsening": drawdown_delta,
                "mean_turnover_delta": turnover_delta,
                "min_required_net_return_delta": policy.min_challenger_net_excess_return,
                "max_allowed_cost_rate_increase": policy.max_cost_rate_increase,
                "max_allowed_drawdown_worsening": policy.max_drawdown_worsening,
                "max_allowed_turnover_increase": policy.max_turnover_increase,
            },
            rollback_strategy_version_id=champion.strategy_version_id,
            proposed_by="champion_service",
        )
        return self._add_or_get_decision(record)

    def propose_rollback(
        self, champion_id: str, rollback_id: str, *, rationale: str
    ) -> ChampionDecision:
        champion = self._strategy(champion_id)
        rollback = self._strategy(rollback_id)
        if self.store.current_state(ObjectType.STRATEGY_VERSION, champion_id) != "champion":
            raise LifecycleError(
                code="ROLLBACK_SOURCE_NOT_CHAMPION",
                message=f"策略版本 {champion_id} 不是当前 champion。",
                blocking=True,
                suggested_action="刷新竞争面板后选择当前 champion。",
            )
        if (
            self.store.current_state(ObjectType.STRATEGY_VERSION, rollback_id)
            != "watching"
            or champion.strategy_name != rollback.strategy_name
        ):
            raise LifecycleError(
                code="ROLLBACK_TARGET_NOT_WATCHING",
                message="回滚目标必须是同一策略槽位的 watching 版本。",
                blocking=True,
                suggested_action="只能回滚到历史保留的 watching 版本。",
            )
        policy = load_champion_policy()
        pairs = self._matching_pairs(
            self._runs(champion_id, RunPurpose.SHADOW),
            self._runs(rollback_id, RunPurpose.SHADOW),
        )
        if len(pairs) < policy.min_shadow_observations:
            raise LifecycleError(
                code="ROLLBACK_SHADOW_EVIDENCE_NOT_READY",
                message=(
                    "当前 champion 与回滚目标的同期影子观察不足："
                    f"{len(pairs)}/{policy.min_shadow_observations}。"
                ),
                blocking=True,
                suggested_action="继续影子运行并补齐同期标签后重新生成回滚证据包。",
            )
        selected = pairs[-policy.min_shadow_observations :]
        comparisons = [self._shadow_delta(left, right) for left, right in selected]
        net_delta = mean(item["net_return_delta"] for item in comparisons)
        cost_delta = mean(item["cost_rate_delta"] for item in comparisons)
        evidence = []
        for left, right in selected:
            evidence.extend([left.evaluation_run_id, right.evaluation_run_id])
        evidence = list(dict.fromkeys(evidence))
        decision_id = f"CD-{_hash(['rollback', champion_id, rollback_id, evidence])[:20].upper()}"
        record = ChampionDecision(
            decision_id=decision_id,
            champion_strategy_version_id=champion_id,
            challenger_strategy_version_id=rollback_id,
            action=DecisionAction.ROLLBACK,
            evidence_run_ids=evidence,
            shadow_window=TimeWindow(
                start=min(left.window.start for left, _right in selected),
                end=max(left.window.end for left, _right in selected),
            ),
            rationale=(
                f"{rationale}；{len(selected)} 期同期影子中回滚目标相对当前版本的"
                f"平均净收益差 {net_delta:.4%}、成本率差 {cost_delta:.4%}。"
            ),
            risk_delta={
                "shadow_observations": len(selected),
                "rollback_target_net_return_delta": net_delta,
                "rollback_target_cost_rate_delta": cost_delta,
            },
            rollback_strategy_version_id=rollback_id,
            proposed_by="champion_service",
        )
        return self._add_or_get_decision(record)

    def approve(self, decision_id: str, *, reason: str, decided_by: str) -> None:
        decision = self.store.get(ObjectType.CHAMPION_DECISION, decision_id)
        assert isinstance(decision, ChampionDecision)
        decision_state = self.store.current_state(
            ObjectType.CHAMPION_DECISION, decision_id
        )
        if decision_state == "applied":
            return
        if decision_state == "proposed":
            self.store.transition(
                ObjectType.CHAMPION_DECISION,
                decision_id,
                "approved",
                actor_type=ActorType.HUMAN,
                actor_ref=decided_by,
                reason=reason,
            )
        elif decision_state != "approved":
            raise LifecycleError(
                code="CHAMPION_DECISION_NOT_APPROVABLE",
                message=f"决策 {decision_id} 当前为 {decision_state}，不能批准或续用。",
                blocking=True,
                suggested_action="只能批准 proposed 决策，或继续应用已 approved 决策。",
            )
        if decision.action not in {DecisionAction.PROMOTE, DecisionAction.ROLLBACK}:
            raise LifecycleError(
                code="CHAMPION_DECISION_ACTION_NOT_APPLICABLE",
                message=f"决策 {decision_id} 的 {decision.action.value} 动作不会切换 champion。",
                blocking=True,
                suggested_action="对 keep/reject 只记录结论，不调用切换端点。",
            )
        target_id = (
            decision.rollback_strategy_version_id
            if decision.action is DecisionAction.ROLLBACK
            else decision.challenger_strategy_version_id
        )
        old_target = (
            "rolled_back"
            if decision.action is DecisionAction.ROLLBACK
            else "watching"
        )
        current_old = self.store.current_state(
            ObjectType.STRATEGY_VERSION,
            decision.champion_strategy_version_id,
        )
        current_target = self.store.current_state(
            ObjectType.STRATEGY_VERSION, target_id
        )
        transitions: list[dict[str, object]] = []
        
        if current_old != old_target:
            transitions.append(
                {
                    "object_type": ObjectType.STRATEGY_VERSION,
                    "object_id": decision.champion_strategy_version_id,
                    "target": old_target,
                    "actor_type": ActorType.SYSTEM,
                    "actor_ref": "champion_service",
                    "reason": (
                        f"人工批准回滚到 {target_id}"
                        if decision.action is DecisionAction.ROLLBACK
                        else f"被 {target_id} 替换，保留为回滚版本"
                    ),
                }
            )
        if current_target != "champion":
            transitions.append(
                {
                    "object_type": ObjectType.STRATEGY_VERSION,
                    "object_id": target_id,
                    "target": "champion",
                    "actor_type": ActorType.HUMAN,
                    "actor_ref": decided_by,
                    "reason": f"执行已批准决策 {decision_id}：{reason}",
                }
            )
        transitions.append(
            {
                "object_type": ObjectType.CHAMPION_DECISION,
                "object_id": decision_id,
                "target": "applied",
                "actor_type": ActorType.SYSTEM,
                "actor_ref": "champion_service",
                "reason": "新旧策略状态已在同一事务中切换",
            }
        )
        self.store.transition_batch(transitions)

    def _add_or_get_decision(self, record: ChampionDecision) -> ChampionDecision:
        try:
            self.store.add(record)
            return record
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_OBJECT_EXISTS":
                raise
        existing = self.store.get(ObjectType.CHAMPION_DECISION, record.decision_id)
        assert isinstance(existing, ChampionDecision)
        return existing

    @staticmethod
    def _matching_pairs(
        left_runs: list[EvaluationRun], right_runs: list[EvaluationRun]
    ) -> list[tuple[EvaluationRun, EvaluationRun]]:
        pairs = [
            (left, right)
            for left in left_runs
            for right in right_runs
            if left.data_snapshot_id == right.data_snapshot_id
            and left.window == right.window
            and left.protocol_hash == right.protocol_hash
        ]
        return sorted(pairs, key=lambda pair: (pair[0].window.end, pair[0].evaluation_run_id))

    def _shadow_delta(
        self, champion: EvaluationRun, challenger: EvaluationRun
    ) -> dict[str, float]:
        left = self._run_metrics(champion)
        right = self._run_metrics(challenger)
        return {
            "net_return_delta": float(right.get("net_return", 0.0))
            - float(left.get("net_return", 0.0)),
            "cost_rate_delta": float(right.get("estimated_cost_rate", 0.0))
            - float(left.get("estimated_cost_rate", 0.0)),
            "drawdown_worsening": abs(float(right.get("max_drawdown", 0.0)))
            - abs(float(left.get("max_drawdown", 0.0))),
            "turnover_delta": float(right.get("turnover", 0.0))
            - float(left.get("turnover", 0.0)),
        }

    def _run_metrics(self, run: EvaluationRun) -> dict[str, Any]:
        event = next(
            (
                item
                for item in reversed(
                    self.store.events(ObjectType.EVALUATION_RUN, run.evaluation_run_id)
                )
                if item["to_state"] == "completed"
            ),
            None,
        )
        metrics = (event or {}).get("payload", {}).get("metrics")
        required = {
            "net_return",
            "estimated_cost_rate",
            "holding_days",
            "max_drawdown",
            "turnover",
        }
        if not isinstance(metrics, dict) or not required.issubset(metrics):
            raise LifecycleError(
                code="CHAMPION_SHADOW_METRICS_MISSING",
                message=(
                    f"影子运行 {run.evaluation_run_id} 缺少净收益、成本率或观察期指标。"
                ),
                blocking=True,
                suggested_action="用当前治理口径生成新的到期影子结果，不得仅凭旧状态晋升。",
            )
        expected_days = load_champion_policy().shadow_observation_days
        if int(metrics["holding_days"]) != expected_days:
            raise LifecycleError(
                code="CHAMPION_SHADOW_WINDOW_POLICY_MISMATCH",
                message=(
                    f"影子运行 {run.evaluation_run_id} 的观察期为 {metrics['holding_days']}，"
                    f"当前治理口径为 {expected_days}。"
                ),
                blocking=True,
                suggested_action="仅使用同一治理配置产生的同期影子证据。",
            )
        return metrics

    def _champion(self, strategy_name: str) -> StrategyVersion | None:
        for row in self.store.list_objects(ObjectType.STRATEGY_VERSION, limit=5000):
            if row["state"] != "champion":
                continue
            record = self._strategy(row["object_id"])
            if record.strategy_name == strategy_name:
                return record
        return None

    def _strategy(self, strategy_id: str) -> StrategyVersion:
        record = self.store.get(ObjectType.STRATEGY_VERSION, strategy_id)
        assert isinstance(record, StrategyVersion)
        return record

    def _runs(self, strategy_id: str, purpose: RunPurpose) -> list[EvaluationRun]:
        result = []
        for row in reversed(
            self.store.list_objects(ObjectType.EVALUATION_RUN, limit=5000)
        ):
            if row["state"] != "completed":
                continue
            run = self.store.get(ObjectType.EVALUATION_RUN, row["object_id"])
            assert isinstance(run, EvaluationRun)
            if run.subject_id == strategy_id and run.purpose is purpose:
                result.append(run)
        return result

__all__ = ["ChampionService"]
