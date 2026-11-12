from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any

from exceptions import LifecycleError
from pydantic import ValidationError
from portfolio.accounts import AccountRegistry
from portfolio.models import Action, Trade
from portfolio.recommend import holdings_digest
from portfolio.store import PortfolioStore

from .models import (
    ExecutionFill,
    ExecutionRecord,
    ObjectType,
    PortfolioPlan,
    StrategyVersion,
)
from .states import ActorType
from .store import LifecycleStore

class ExecutionFeedbackService:
    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    def classify(self, trade: Trade, plan_id: str) -> tuple[str, str]:
        plan = self._plan(plan_id)
        if trade.account_id not in plan.account_ids:
            raise LifecycleError(
                code="EXECUTION_PLAN_ACCOUNT_MISMATCH",
                message=f"成交账户 {trade.account_id} 不属于计划 {plan_id}。",
                blocking=True,
                suggested_action="选择该账户的调仓计划，或将本笔标记为计划外交易。",
            )
        plan_state = self.store.current_state(ObjectType.PORTFOLIO_PLAN, plan_id)
        if plan_state not in {"active", "partially_executed"} and not self._was_confirmed(
            plan_id
        ):
            raise LifecycleError(
                code="EXECUTION_PLAN_NOT_ACTIVE",
                message=f"计划 {plan_id} 尚未人工确认或已终止。",
                blocking=True,
                suggested_action="只核对已确认且在执行窗口内的 champion 计划。",
            )
        side = self._side(trade.action)
        order = next(
            (
                item
                for item in plan.proposed_orders
                if item.instrument_id == trade.instrument_id and item.side == side
            ),
            None,
        )
        if order is None:
            return "unplanned", "标的或方向不在已确认计划中"
        if not self._inside_execution_window(trade.executed_at, plan.execution_window):
            return "unplanned", "实际成交时间不在已确认计划的执行窗口内"
        strategy = self.store.get(ObjectType.STRATEGY_VERSION, plan.strategy_version_id)
        assert isinstance(strategy, StrategyVersion)
        threshold = float(strategy.execution_policy.get("price_deviation_threshold") or 0)
        deviation = abs(float(trade.price) / order.reference_price - 1)
        if threshold and deviation > threshold:
            return "large_deviation", f"成交价偏离参考价 {deviation:.2%}"
        if float(trade.qty) < order.quantity:
            return "partial", "实际成交数量小于建议数量"
        return "planned", "标的、方向、数量与价格偏差均在计划内"

    def record(self, trade: Trade, plan_id: str, *, holding_snapshot_ref: str) -> ExecutionRecord:
        record_id = f"XR-{trade.trade_id}"
        try:
            existing = self.store.get(ObjectType.EXECUTION_RECORD, record_id)
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_REFERENCE_NOT_FOUND":
                raise
        else:
            assert isinstance(existing, ExecutionRecord)
            if existing.portfolio_plan_id != plan_id:
                raise LifecycleError(
                    code="EXECUTION_IDEMPOTENCY_KEY_CONFLICT",
                    message=f"成交 {trade.trade_id} 已关联其他闭环计划。",
                    blocking=True,
                    suggested_action="不得复用成交 ID；核对原始券商流水和计划映射。",
                )
            return existing
        match_status, reason = self.classify(trade, plan_id)
        plan = self._plan(plan_id)
        fill = ExecutionFill(
            execution_id=trade.trade_id,
            instrument_id=trade.instrument_id,
            side=self._side(trade.action),
            quantity=float(trade.qty),
            price=float(trade.price),
            executed_at=trade.executed_at,
            fees=float(trade.fee + trade.tax + trade.other_cost),
            match_status=match_status,
        )
        record = ExecutionRecord(
            execution_record_id=record_id,
            portfolio_plan_id=plan_id,
            account_id=trade.account_id,
            fills=[fill],
            unfilled_order_refs=self._remaining_order_refs(plan, [fill]),
            source="manual",
            holding_snapshot_ref=holding_snapshot_ref,
            reconciliation_summary={"match_status": match_status, "reason": reason},
        )
        self.store.add(record)
        self.store.transition(
            ObjectType.EXECUTION_RECORD,
            record_id,
            "reconciled",
            actor_type=ActorType.SYSTEM,
            actor_ref="execution_feedback",
            reason=reason,
        )
        self._advance_plan(plan_id)
        return record

    def recover_manual_trades(
        self, *, account_ids: list[str] | None = None
    ) -> dict[str, Any]:
        
        registry = AccountRegistry()
        portfolio = PortfolioStore()
        wanted = set(account_ids or [])
        recovered: list[str] = []
        skipped = 0
        errors: list[dict[str, str]] = []
        for account in registry.list():
            if wanted and account.account_id not in wanted:
                continue
            trades = portfolio.read_trades(
                account.account_id,
                market=account.market,
                instrument_class=account.instrument_class,
            )
            holdings = portfolio.read_holdings(
                account.account_id,
                market=account.market,
                instrument_class=account.instrument_class,
            )
            snapshot_ref = (
                f"portfolio://{account.account_id}/holdings/{holdings_digest(holdings)}"
            )
            for raw_row in trades.to_dict(orient="records"):
                raw = {
                    key: (None if self._missing(value) else value)
                    for key, value in raw_row.items()
                }
                plan_id = str(raw.get("related_plan_id") or "").strip()
                if not plan_id:
                    continue
                trade_id = str(raw.get("trade_id") or "")
                try:
                    self.store.get(ObjectType.EXECUTION_RECORD, f"XR-{trade_id}")
                except LifecycleError as exc:
                    if exc.code != "LIFECYCLE_REFERENCE_NOT_FOUND":
                        raise
                else:
                    skipped += 1
                    continue
                raw["reason"] = raw.get("reason") or ""
                raw["note"] = raw.get("note") or ""
                try:
                    trade = Trade.model_validate(raw)
                    record = self.record(
                        trade,
                        plan_id,
                        holding_snapshot_ref=snapshot_ref,
                    )
                    recovered.append(record.execution_record_id)
                except ValidationError as exc:
                    errors.append(
                        {
                            "trade_id": trade_id,
                            "code": "EXECUTION_RECOVERY_TRADE_INVALID",
                            "message": str(exc.errors()[0].get("msg", exc)),
                        }
                    )
                except LifecycleError as exc:
                    errors.append(
                        {
                            "trade_id": trade_id,
                            "code": exc.code,
                            "message": exc.message,
                        }
                    )
        return {"recovered": recovered, "skipped": skipped, "errors": errors}

    @staticmethod
    def _missing(value: Any) -> bool:
        if value is None:
            return True
        try:
            return bool(value != value)
        except (TypeError, ValueError):
            return type(value).__name__ in {"NAType", "NaTType"}

    def record_broker(
        self,
        *,
        plan_id: str,
        account_id: str,
        fills: list[dict[str, Any]],
        holding_snapshot_ref: str,
    ) -> ExecutionRecord:
        plan = self._plan(plan_id)
        if account_id not in plan.account_ids:
            raise LifecycleError(
                code="BROKER_EXECUTION_PLAN_ACCOUNT_MISMATCH",
                message=f"券商回报账户 {account_id} 不属于计划 {plan_id}。",
                blocking=True,
                suggested_action="核对券商账户映射和 PortfolioPlan.account_ids。",
            )
        digest = hashlib.sha256(
            "|".join(sorted(str(item["execution_id"]) for item in fills)).encode(
                "utf-8"
            )
        ).hexdigest()[:20].upper()
        record_id = f"XR-BRK-{digest}"
        try:
            existing = self.store.get(ObjectType.EXECUTION_RECORD, record_id)
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_REFERENCE_NOT_FOUND":
                raise
        else:
            assert isinstance(existing, ExecutionRecord)
            if (
                existing.portfolio_plan_id != plan_id
                or existing.account_id != account_id
            ):
                raise LifecycleError(
                    code="BROKER_EXECUTION_IDEMPOTENCY_KEY_CONFLICT",
                    message="券商 execution_id 集合已用于其他账户或计划。",
                    blocking=True,
                    suggested_action="停止导入并核对券商回报去重键。",
                )
            return existing
        plan_state = self.store.current_state(ObjectType.PORTFOLIO_PLAN, plan_id)
        if plan_state not in {"active", "partially_executed"} and not self._was_confirmed(
            plan_id
        ):
            raise LifecycleError(
                code="BROKER_EXECUTION_PLAN_NOT_ACTIVE",
                message=f"计划 {plan_id} 未在可执行状态。",
                blocking=True,
                suggested_action="券商适配器只能上报已人工确认的 champion 计划。",
            )
        strategy = self.store.get(
            ObjectType.STRATEGY_VERSION, plan.strategy_version_id
        )
        assert isinstance(strategy, StrategyVersion)
        threshold = float(
            strategy.execution_policy.get("price_deviation_threshold") or 0
        )
        parsed: list[ExecutionFill] = []
        for raw in fills:
            instrument = str(raw["instrument_id"])
            side = str(raw["side"])
            quantity = float(raw["quantity"])
            price = float(raw["price"])
            order = next(
                (
                    item
                    for item in plan.proposed_orders
                    if item.instrument_id == instrument and item.side == side
                ),
                None,
            )
            if order is None:
                status = "unplanned"
            elif not self._inside_execution_window(
                raw["executed_at"], plan.execution_window
            ):
                status = "unplanned"
            elif threshold and abs(price / order.reference_price - 1) > threshold:
                status = "large_deviation"
            elif quantity + 1e-8 < order.quantity:
                status = "partial"
            else:
                status = "planned"
            parsed.append(ExecutionFill.model_validate({**raw, "match_status": status}))
        record = ExecutionRecord(
            execution_record_id=record_id,
            portfolio_plan_id=plan_id,
            account_id=account_id,
            fills=parsed,
            unfilled_order_refs=self._remaining_order_refs(plan, parsed),
            source="broker_adapter",
            holding_snapshot_ref=holding_snapshot_ref,
            reconciliation_summary={
                "fill_count": len(parsed),
                "unplanned_count": sum(
                    fill.match_status == "unplanned" for fill in parsed
                ),
                "large_deviation_count": sum(
                    fill.match_status == "large_deviation" for fill in parsed
                ),
            },
        )
        try:
            self.store.add(record)
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_OBJECT_EXISTS":
                raise
            existing = self.store.get(ObjectType.EXECUTION_RECORD, record_id)
            assert isinstance(existing, ExecutionRecord)
            return existing
        self.store.transition(
            ObjectType.EXECUTION_RECORD,
            record_id,
            "reconciled",
            actor_type=ActorType.BROKER_ADAPTER,
            actor_ref="broker_execution_adapter",
            reason="券商成交回报已与人工确认计划核对",
        )
        self._advance_plan(plan_id)
        return record

    def _remaining_order_refs(
        self, plan: PortfolioPlan, pending_fills: list[ExecutionFill]
    ) -> list[str]:
        actual: dict[tuple[str, str], float] = defaultdict(float)
        for row in self.store.list_objects(ObjectType.EXECUTION_RECORD, limit=5000):
            record = self.store.get(ObjectType.EXECUTION_RECORD, row["object_id"])
            assert isinstance(record, ExecutionRecord)
            if record.portfolio_plan_id != plan.portfolio_plan_id:
                continue
            for fill in record.fills:
                actual[(fill.instrument_id, fill.side)] += fill.quantity
        for pending_fill in pending_fills:
            actual[(pending_fill.instrument_id, pending_fill.side)] += pending_fill.quantity
        refs = []
        for order in plan.proposed_orders:
            remaining = max(
                0.0,
                order.quantity - actual[(order.instrument_id, order.side)],
            )
            if remaining > 1e-8:
                refs.append(
                    f"{order.instrument_id}:{order.side}:remaining={remaining:.8f}"
                )
        return refs

    def _advance_plan(self, plan_id: str) -> None:
        plan = self._plan(plan_id)
        expected = {
            (item.instrument_id, item.side): item.quantity
            for item in plan.proposed_orders
        }
        actual: dict[tuple[str, str], float] = defaultdict(float)
        for row in self.store.list_objects(ObjectType.EXECUTION_RECORD, limit=5000):
            record = self.store.get(ObjectType.EXECUTION_RECORD, row["object_id"])
            assert isinstance(record, ExecutionRecord)
            if record.portfolio_plan_id != plan_id:
                continue
            for fill in record.fills:
                if (fill.instrument_id, fill.side) in expected:
                    actual[(fill.instrument_id, fill.side)] += fill.quantity
        completed = bool(expected) and all(
            actual[key] + 1e-8 >= quantity for key, quantity in expected.items()
        )
        target = "executed" if completed else "partially_executed"
        current = self.store.current_state(ObjectType.PORTFOLIO_PLAN, plan_id)
        if current not in {"active", "partially_executed"}:
            return
        if current == target:
            return
        self.store.transition(
            ObjectType.PORTFOLIO_PLAN,
            plan_id,
            target,
            actor_type=ActorType.SYSTEM,
            actor_ref="execution_feedback",
            reason=("全部建议交易已成交" if completed else "已收到部分成交回报"),
        )

    def _plan(self, plan_id: str) -> PortfolioPlan:
        plan = self.store.get(ObjectType.PORTFOLIO_PLAN, plan_id)
        assert isinstance(plan, PortfolioPlan)
        return plan

    def _was_confirmed(self, plan_id: str) -> bool:
        return any(
            event["to_state"] == "confirmed"
            for event in self.store.events(ObjectType.PORTFOLIO_PLAN, plan_id)
        )

    @staticmethod
    def _inside_execution_window(executed_at, raw_window: str) -> bool:
        from datetime import UTC, datetime, time

        try:
            start_raw, end_raw = raw_window.split("..", 1)
            start = datetime.fromisoformat(start_raw)
            end = datetime.fromisoformat(end_raw)
        except (TypeError, ValueError):
            return False
        value = executed_at
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if end.timetz().replace(tzinfo=None) == time.min:
            end = datetime.combine(end.date(), time.max, tzinfo=end.tzinfo)
        return start.astimezone(UTC) <= value.astimezone(UTC) <= end.astimezone(UTC)

    @staticmethod
    def _side(action: Action) -> str:
        return "buy" if action in {Action.OPEN, Action.INCREASE} else "sell"

__all__ = ["ExecutionFeedbackService"]
