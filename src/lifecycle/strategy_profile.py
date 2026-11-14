"""Resolve frozen lifecycle factor inputs for daily portfolio calculations."""
from __future__ import annotations
import json
from typing import Any
from backtest.signal import RuntimeFactor
from exceptions import LifecycleError
from paths import workspace
from strategy import StrategyProfileStore, ResolvedStrategyProfile
from .models import StrategyVersion, FactorVersion, ObjectType

class StrategyProfileAdapter:
    def __init__(self, store):
        self.store = store
        self.profile_store = StrategyProfileStore()

    def profile_for(self, strategy: StrategyVersion) -> ResolvedStrategyProfile:
        base = self.profile_store.resolve(strategy.strategy_name)
        payload = base.model_dump(mode="json")
        names = self._factor_names(strategy)
        payload["factors"] = {
            "weighting": "equal",
            "items": [
                {"name": names[item.factor_version_id], "weight_override": str(item.weight)}
                for item in strategy.factor_inputs
            ],
        }
        portfolio = strategy.portfolio_policy
        payload["position_constraint"].update(
            {
                "top_n": portfolio["top_n"],
                "max_weight_per_name": str(portfolio["single_name_cap"]),
                "min_cash_ratio": str(portfolio["cash_ratio"]),
            }
        )
        payload["constraints"].update(max_positions=portfolio["top_n"],max_weight=float(portfolio["single_name_cap"]),cash_reserve=float(portfolio["cash_ratio"]))
        payload["rebalance_frequency"] = portfolio["rebalance_frequency"]
        payload["holding_period"] = portfolio["minimum_holding_period"]
        risk = strategy.risk_policy
        payload["risk_control"].update(
            {
                "max_single_name_pct": str(risk["max_single_name_pct"]),
                "max_industry_pct": str(risk["max_industry_pct"]),
            }
        )
        payload["constraints"].update(max_weight=min(float(portfolio["single_name_cap"]),float(risk["max_single_name_pct"])),max_industry_weight=float(risk["max_industry_pct"]))
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
                standardization=tuple(item.transformations),
                missing_handling=artifact.get("missing_handling", "drop"),
                parameters=dict(artifact.get("parameters") or {}),
            )
        return result

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

