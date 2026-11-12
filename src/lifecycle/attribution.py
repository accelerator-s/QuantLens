from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import pandas as pd

from exceptions import LifecycleError

from .models import (
    AttributionComponent,
    AttributionLayer,
    AttributionRecord,
    ExecutionRecord,
    ObjectType,
    PortfolioPlan,
    StrategyVersion,
    TimeWindow,
)
from .states import ActorType
from .store import LifecycleStore

def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class AttributionService:
    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    def mature(self, *, as_of: str) -> dict[str, Any]:
        end_limit = date.fromisoformat(as_of)
        result = []
        for row in self.store.list_objects(ObjectType.EXECUTION_RECORD, limit=5000):
            if row["state"] != "reconciled":
                continue
            execution = self.store.get(ObjectType.EXECUTION_RECORD, row["object_id"])
            assert isinstance(execution, ExecutionRecord)
            attribution = self._attribute(execution, end_limit)
            if attribution is None:
                continue
            try:
                self.store.add(attribution)
            except LifecycleError as exc:
                if exc.code != "LIFECYCLE_OBJECT_EXISTS":
                    raise
            if self.store.current_state(
                ObjectType.ATTRIBUTION_RECORD,
                attribution.attribution_record_id,
            ) == "recorded":
                self.store.transition(
                    ObjectType.ATTRIBUTION_RECORD,
                    attribution.attribution_record_id,
                    "validated",
                    actor_type=ActorType.SYSTEM,
                    actor_ref="attribution_service",
                    reason="价格标签、计划与成交证据已完整核验",
                )
            self.store.transition(
                ObjectType.EXECUTION_RECORD,
                execution.execution_record_id,
                "attributed",
                actor_type=ActorType.SYSTEM,
                actor_ref="attribution_service",
                reason=f"已生成归因 {attribution.attribution_record_id}",
            )
            result.append(attribution)
        return {"items": result}

    def _attribute(
        self, execution: ExecutionRecord, end_limit: date
    ) -> AttributionRecord | None:
        plan = self.store.get(ObjectType.PORTFOLIO_PLAN, execution.portfolio_plan_id)
        assert isinstance(plan, PortfolioPlan)
        strategy = self.store.get(ObjectType.STRATEGY_VERSION, plan.strategy_version_id)
        assert isinstance(strategy, StrategyVersion)
        holding_days = int(strategy.portfolio_policy["minimum_holding_period"])
        from panel import load_panel

        instruments = sorted(
            {item.instrument_id for item in plan.targets}
            | {fill.instrument_id for fill in execution.fills}
        )
        closes = load_panel(
            ["close"], start=plan.trading_date.isoformat(), end=end_limit.isoformat()
        )["close"].reindex(columns=instruments).ffill()
        if len(closes) <= holding_days:
            return None
        label_end = closes.index[holding_days].date()
        end_prices = closes.iloc[holding_days]
        start_prices = closes.iloc[0]
        if any(
            pd.isna(start_prices.get(instrument))
            or pd.isna(end_prices.get(instrument))
            for instrument in instruments
        ):
            raise LifecycleError(
                code="ATTRIBUTION_PRICE_MISSING",
                message=f"成交 {execution.execution_record_id} 的到期归因价格不完整。",
                blocking=True,
                suggested_action="补齐行情后重新运行到期归因。",
            )

        signal_return = sum(
            target.target_weight
            * (
                float(end_prices[target.instrument_id])
                / float(start_prices[target.instrument_id])
                - 1
            )
            for target in plan.targets
        )
        market_returns = load_panel(
            ["ret_wd"],
            start=plan.trading_date.isoformat(),
            end=label_end.isoformat(),
        )["ret_wd"]
        daily_market = market_returns.mean(axis=1, skipna=True).iloc[1 : holding_days + 1]
        benchmark_return = 1.0
        for value in daily_market.dropna():
            benchmark_return *= 1.0 + float(value)
        benchmark_return -= 1.0
        regime = (
            "risk_on"
            if benchmark_return >= 0.03
            else "risk_off" if benchmark_return <= -0.03 else "neutral"
        )
        signal_alpha = signal_return - benchmark_return
        factor_attribution = {
            f"factor:{item.factor_version_id}": signal_alpha * item.weight
            for item in strategy.factor_inputs
        }
        notional = sum(fill.quantity * fill.price for fill in execution.fills)
        if notional <= 0:
            raise LifecycleError(
                code="ATTRIBUTION_NOTIONAL_INVALID",
                message=f"成交 {execution.execution_record_id} 名义金额不为正数。",
                blocking=True,
                suggested_action="修正成交数量或价格。",
            )
        planned_actual = 0.0
        planned_reference = 0.0
        unplanned_actual = 0.0
        fees = 0.0
        for fill in execution.fills:
            end_price = float(end_prices[fill.instrument_id])
            direction = 1.0 if fill.side == "buy" else -1.0
            contribution = (
                direction * fill.quantity * (end_price - fill.price) - fill.fees
            ) / notional
            fees += fill.fees / notional
            order = next(
                (
                    item
                    for item in plan.proposed_orders
                    if item.instrument_id == fill.instrument_id and item.side == fill.side
                ),
                None,
            )
            if order is None or fill.match_status == "unplanned":
                unplanned_actual += contribution
                continue
            planned_actual += contribution
            planned_reference += (
                direction
                * fill.quantity
                * (end_price - order.reference_price)
                / notional
            )
        constraint = planned_reference - signal_return
        execution_drag = planned_actual - planned_reference
        realized = planned_actual + unplanned_actual
        components = [
            AttributionComponent(
                layer=AttributionLayer.SIGNAL,
                contribution=signal_alpha,
                evidence={
                    "portfolio_plan_id": plan.portfolio_plan_id,
                    "factor_version_ids": ",".join(
                        item.factor_version_id for item in strategy.factor_inputs
                    ),
                    **factor_attribution,
                },
                explanation=(
                    "目标组合后验收益扣除同期等权市场收益后的信号贡献；"
                    "evidence 中按策略冻结权重分配到各 FactorVersion"
                ),
            ),
            AttributionComponent(
                layer=AttributionLayer.CONSTRAINT,
                contribution=constraint,
                evidence={"planned_reference_return": planned_reference},
                explanation="整手、现金、持仓和可交易性约束使计划偏离纯信号的影响",
            ),
            AttributionComponent(
                layer=AttributionLayer.EXECUTION,
                contribution=execution_drag,
                evidence={"fee_drag": fees},
                explanation="实际成交价、部分成交和费税相对参考执行的影响",
            ),
            AttributionComponent(
                layer=AttributionLayer.USER_OVERRIDE,
                contribution=unplanned_actual,
                evidence={"source": execution.source},
                explanation="计划外成交或用户主动改动的影响",
            ),
            AttributionComponent(
                layer=AttributionLayer.MARKET_REGIME,
                contribution=benchmark_return,
                evidence={
                    "regime": regime,
                    "benchmark": "PIT universe equal_weight_ret_wd",
                },
                explanation="同期 PIT 样本池等权市场收益及风险状态贡献",
            ),
        ]
        residual = realized - sum(item.contribution for item in components)
        record_id = f"AR-{_hash([execution.execution_record_id, label_end])[:20].upper()}"
        return AttributionRecord(
            attribution_record_id=record_id,
            portfolio_plan_id=plan.portfolio_plan_id,
            execution_record_id=execution.execution_record_id,
            strategy_version_id=strategy.strategy_version_id,
            label_window=TimeWindow(start=plan.trading_date, end=label_end),
            realized_return=realized,
            benchmark_return=benchmark_return,
            components=components,
            residual=residual,
            metric_artifact_ref=f"lifecycle://execution_record/{execution.execution_record_id}",
        )

__all__ = ["AttributionService"]
