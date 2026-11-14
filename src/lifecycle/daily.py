from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, UTC
from typing import Any

from exceptions import LifecycleError, QuantTrainingError
from portfolio.accounts import AccountRegistry
from portfolio.models import Action
from portfolio.recommend import build_plan
from .strategy_profile import StrategyProfileAdapter

from .models import (
    EvaluationRun,
    ObjectType,
    PortfolioPlan,
    PortfolioTarget,
    ProposedOrder,
    RunPurpose,
    StrategyVersion,
    SubjectType,
    TimeWindow,
)
from .snapshots import DataSnapshotService
from .governance import load_champion_policy
from .states import ActorType
from .store import LifecycleStore

def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class DailyOperationsService:
    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()
        self.strategy_services = StrategyProfileAdapter(self.store)

    def run(
        self, *, account_ids: list[str] | None = None, as_of: str | None = None
    ) -> dict[str, Any]:
        from .execution import ExecutionFeedbackService

        execution_recovery = ExecutionFeedbackService(self.store).recover_manual_trades(
            account_ids=account_ids
        )
        snapshot = DataSnapshotService(self.store).create(require_strict=False)
        accounts = AccountRegistry().list()
        if account_ids:
            wanted = set(account_ids)
            accounts = [item for item in accounts if item.account_id in wanted]
            missing = sorted(wanted - {item.account_id for item in accounts})
            if missing:
                raise LifecycleError(
                    code="DAILY_ACCOUNT_NOT_FOUND",
                    message=f"每日运行指定了不存在的账户：{'、'.join(missing)}",
                    blocking=True,
                    suggested_action="从持仓页选择已建立的账户。",
                )
        strategies = self._live_strategies()
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for strategy in strategies:
            profile = self.strategy_services.profile_for(strategy)
            runtime = self.strategy_services.runtime_factors(strategy)
            rule = profile.trading_rules.market_rule
            for account in accounts:
                if account.market != profile.market:
                    continue
                try:
                    built = build_plan(
                        account=account,
                        profile=profile,
                        as_of=as_of,
                        runtime_factors=runtime,
                        require_active_profile=False,
                    )
                    canonical = self._canonical_plan(
                        strategy,
                        snapshot.snapshot_id,
                        built.plan,
                        built.signal_date,
                        reference_portfolio_value=float(built.nav),
                    )
                    self._expire_previous(canonical)
                    try:
                        self.store.add(canonical)
                    except LifecycleError as exc:
                        if exc.code != "LIFECYCLE_OBJECT_EXISTS":
                            raise
                    items.append(
                        {
                            "portfolio_plan_id": canonical.portfolio_plan_id,
                            "strategy_version_id": strategy.strategy_version_id,
                            "strategy_state": self.store.current_state(
                                ObjectType.STRATEGY_VERSION,
                                strategy.strategy_version_id,
                            ),
                            "account_id": account.account_id,
                            "market_rule": rule,
                            "orders": len(canonical.proposed_orders),
                        }
                    )
                except QuantTrainingError as exc:
                    errors.append(
                        {
                            "strategy_version_id": strategy.strategy_version_id,
                            "account_id": account.account_id,
                            "code": exc.code,
                            "message": exc.message,
                        }
                    )
        return {
            "data_snapshot_id": snapshot.snapshot_id,
            "execution_recovery": execution_recovery,
            "plans": items,
            "errors": errors,
        }

    def observe_matured_shadows(self, *, as_of: str) -> dict[str, Any]:
        end = date.fromisoformat(as_of)
        completed: list[dict[str, Any]] = []
        for row in self.store.list_objects(ObjectType.PORTFOLIO_PLAN, limit=5000):
            plan = self.store.get(ObjectType.PORTFOLIO_PLAN, row["object_id"])
            assert isinstance(plan, PortfolioPlan)
            strategy = self.store.get(
                ObjectType.STRATEGY_VERSION, plan.strategy_version_id
            )
            assert isinstance(strategy, StrategyVersion)
            if self.store.current_state(
                ObjectType.STRATEGY_VERSION, strategy.strategy_version_id
            ) not in {"challenger", "champion", "watching"}:
                continue
            holding_days = load_champion_policy().shadow_observation_days
            label = self._shadow_metrics(plan, end=end, holding_days=holding_days)
            if label is None:
                continue
            run_id = f"ER-SHD-{_hash([plan.portfolio_plan_id, label['end']])[:20].upper()}"
            if self._exists(ObjectType.EVALUATION_RUN, run_id):
                continue
            digest = _hash(label)
            record = EvaluationRun(
                evaluation_run_id=run_id,
                subject_type=SubjectType.STRATEGY,
                subject_id=strategy.strategy_version_id,
                research_program_id=strategy.research_program_id,
                data_snapshot_id=plan.data_snapshot_id,
                purpose=RunPurpose.SHADOW,
                window=TimeWindow(start=label["start"], end=label["end"]),
                protocol_hash=self.store.get(
                    ObjectType.RESEARCH_PROGRAM, strategy.research_program_id
                ).protocol_hash,
                artifact_ref=f"lifecycle://portfolio_plan/{plan.portfolio_plan_id}",
                artifact_hash=digest,
            )
            self.store.add(record)
            self._transition(ObjectType.EVALUATION_RUN, run_id, "running", "影子标签到期")
            self._transition(
                ObjectType.EVALUATION_RUN,
                run_id,
                "completed",
                "影子组合实际后验收益已补写",
                payload={
                    "outcome": "passed",
                    "metrics_digest": digest,
                    "artifact_hash": digest,
                    "metrics": label,
                },
            )
            completed.append(
                {
                    "evaluation_run_id": run_id,
                    "strategy_version_id": strategy.strategy_version_id,
                    "portfolio_plan_id": plan.portfolio_plan_id,
                    "metrics": label,
                }
            )
        return {"items": completed}

    def _canonical_plan(
        self,
        strategy,
        snapshot_id: str,
        plan,
        signal_date: str,
        *,
        reference_portfolio_value: float,
    ) -> PortfolioPlan:
        semantic = [snapshot_id, strategy.strategy_version_id, *plan.account_ids, signal_date,
                    plan.current_holdings_digest, reference_portfolio_value,
                    [item.model_dump(mode="json") for item in plan.target_holdings],
                    [item.model_dump(mode="json") for item in plan.suggested_trades], plan.caveats]
        plan_id = f"PP-{_hash(semantic)[:20].upper()}"
        targets = [
            PortfolioTarget(
                instrument_id=item.instrument_id,
                target_quantity=float(item.qty),
                target_weight=float(item.weight or 0),
            )
            for item in plan.target_holdings
        ]
        orders = [
            ProposedOrder(
                instrument_id=item.instrument_id,
                side=(
                    "buy"
                    if item.action in {Action.OPEN, Action.INCREASE}
                    else "sell"
                ),
                quantity=float(item.qty),
                price_window=(
                    f"{plan.effective_window.start.isoformat()}.."
                    f"{plan.effective_window.end.isoformat()}"
                ),
                reference_price=float(item.reference_price),
                estimated_cost=float(item.estimated_cost),
                reason=f"{strategy.strategy_version_id} 目标仓位差额",
            )
            for item in plan.suggested_trades
        ]
        contributions = {
            target.instrument_id: {
                item.factor_version_id: item.weight
                for item in strategy.factor_inputs
            }
            for target in targets
        }
        return PortfolioPlan(
            portfolio_plan_id=plan_id,
            trading_date=date.fromisoformat(signal_date),
            account_ids=plan.account_ids,
            strategy_version_id=strategy.strategy_version_id,
            data_snapshot_id=snapshot_id,
            current_holding_snapshot_refs=[
                f"portfolio://{plan.account_ids[0]}/holdings/{plan.current_holdings_digest}"
            ],
            signal_as_of=datetime.combine(
                date.fromisoformat(signal_date), time.max, tzinfo=UTC
            ),
            execution_window=(
                f"{plan.effective_window.start.isoformat()}.."
                f"{plan.effective_window.end.isoformat()}"
            ),
            reference_portfolio_value=reference_portfolio_value,
            targets=targets,
            proposed_orders=orders,
            factor_contributions=contributions,
            constraint_adjustments={
                "strategy_state": self.store.current_state(
                    ObjectType.STRATEGY_VERSION, strategy.strategy_version_id
                )
            },
            estimated_costs={
                key: float(value) for key, value in plan.estimated_fees_by_currency.items()
            },
            caveats=plan.caveats,
        )

    def _shadow_metrics(
        self, plan: PortfolioPlan, *, end: date, holding_days: int
    ) -> dict[str, Any] | None:
        if end <= plan.trading_date or not plan.targets:
            return None
        from panel import load_panel

        returns = load_panel(
            ["ret_wd"], start=plan.trading_date.isoformat(), end=end.isoformat()
        )["ret_wd"]
        returns = returns.reindex(columns=[item.instrument_id for item in plan.targets])
        returns = returns.loc[returns.index.date > plan.trading_date]
        if len(returns) < holding_days:
            return None
        returns = returns.iloc[:holding_days]
        valid = returns.notna().any(axis=0)
        if not valid.any():
            raise LifecycleError(
                code="SHADOW_PRICE_LABEL_MISSING",
                message=f"影子计划 {plan.portfolio_plan_id} 无法获得到期复权收益。",
                blocking=True,
                suggested_action="补齐行情缓存后重跑影子标签任务。",
            )
        returns = returns.loc[:, valid]
        weights = {item.instrument_id: item.target_weight for item in plan.targets}
        daily = returns.fillna(0.0).mul(
            [weights[str(instrument)] for instrument in returns.columns], axis=1
        ).sum(axis=1)
        nav = (1.0 + daily).cumprod()
        gross = float(nav.iloc[-1] - 1.0)
        max_drawdown = float((nav / nav.cummax() - 1.0).min())
        cost = sum(plan.estimated_costs.values())
        cost_rate = (
            cost / plan.reference_portfolio_value
            if plan.reference_portfolio_value > 0
            else 0.0
        )
        label_end = returns.index[-1].date()
        turnover = (
            sum(
                order.quantity * order.reference_price
                for order in plan.proposed_orders
                if order.side == 'buy'
            )
            / plan.reference_portfolio_value
            if plan.reference_portfolio_value > 0
            else 0.0
        )
        return {
            "start": returns.index[0].date(),
            "end": label_end,
            "holding_days": holding_days,
            "gross_return": gross,
            "estimated_cost_amount": cost,
            "estimated_cost_rate": cost_rate,
            "net_return": gross - cost_rate,
            "sharpe": float(daily.mean()/daily.std()*244**.5) if daily.std()>0 else 0.,
            "max_drawdown": max_drawdown,
            "turnover": turnover,
            "reference_portfolio_value": plan.reference_portfolio_value,
            "execution_caveat_count": len(plan.caveats),
            "target_weight_coverage": sum(
                weights[str(instrument)] for instrument in returns.columns
            ),
            "note": "影子组合按固定治理观察期、目标权重和 PIT 复权收益计算，未模拟真实成交。",
        }

    def _live_strategies(self) -> list[StrategyVersion]:
        result = []
        for row in self.store.list_objects(ObjectType.STRATEGY_VERSION, limit=5000):
            if row["state"] not in {"champion", "challenger", "watching"}:
                continue
            record = self.store.get(ObjectType.STRATEGY_VERSION, row["object_id"])
            assert isinstance(record, StrategyVersion)
            result.append(record)
        return result

    def _expire_previous(self, current: PortfolioPlan) -> None:
        for row in self.store.list_objects(ObjectType.PORTFOLIO_PLAN, limit=5000):
            if row["state"] not in {"proposed", "confirmed", "active", "partially_executed"}:
                continue
            previous = self.store.get(ObjectType.PORTFOLIO_PLAN, row["object_id"])
            assert isinstance(previous, PortfolioPlan)
            if (
                previous.strategy_version_id == current.strategy_version_id
                and set(previous.account_ids) == set(current.account_ids)
                and previous.portfolio_plan_id != current.portfolio_plan_id
                and previous.trading_date <= current.trading_date
            ):
                self._transition(
                    ObjectType.PORTFOLIO_PLAN,
                    previous.portfolio_plan_id,
                    "expired",
                    (
                        "当前账户的新计划已生成，旧计划失效；"
                        "未成交和部分成交事实保留供恢复核对"
                    ),
                )

    def _exists(self, kind: ObjectType, object_id: str) -> bool:
        try:
            self.store.get(kind, object_id)
            return True
        except LifecycleError as exc:
            if exc.code == "LIFECYCLE_REFERENCE_NOT_FOUND":
                return False
            raise

    def _transition(
        self,
        kind: ObjectType,
        object_id: str,
        target: str,
        reason: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.transition(
            kind,
            object_id,
            target,
            actor_type=ActorType.SYSTEM,
            actor_ref="daily_operations",
            reason=reason,
            payload=payload,
        )

__all__ = ["DailyOperationsService"]
