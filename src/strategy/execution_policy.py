"""Account-independent execution settings, applied to actual account weights.

Buffers may delay small adjustments; exits and portfolio risk reductions win.
The turnover budget limits buys, matching the backtest's one-sided definition.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    min_trade_weight: float = Field(default=0, ge=0, le=.05)
    no_trade_band: float = Field(default=0, ge=0, le=.5)
    max_buy_turnover: float = Field(default=1, gt=0, le=1)


def policy_targets(targets, current, policy, *, constraints, industries=None, clusters=None):
    """Return weights, never mutate inputs; removed names always exit.

    Missing classifications disable sell buffering, rather than weakening caps.
    A lower aggregate exposure target also disables sell buffering. No threshold
    or budget is applied to forced exits outside this function.
    """
    policy = ExecutionPolicy.model_validate(policy)
    desired = {k: Decimal(str(v)) for k, v in targets.items()}
    held = {k: Decimal(str(v)) for k, v in current.items()}
    adjusted = dict(desired)
    reducing = sum(desired.values()) < sum(held.values())
    buffered_sells = []
    for code, target in desired.items():
        weight = held.get(code, Decimal(0))
        delta = target - weight
        threshold = max(Decimal(str(policy.min_trade_weight)),
                        target * Decimal(str(policy.no_trade_band)))
        if abs(delta) >= threshold or delta == 0:
            continue
        if delta < 0:
            if (reducing or industries is None or clusters is None
                    or code not in industries or code not in clusters):
                continue
            buffered_sells.append(code)
        adjusted[code] = weight

    def valid(weights):
        if sum(weights.values()) > sum(desired.values()) + Decimal('1e-12'):
            return False
        if any(w > Decimal(str(constraints.max_weight)) + Decimal('1e-12') for w in weights.values()):
            return False
        for mapping, limit in [(industries, constraints.max_industry_weight),
                               (clusters, constraints.max_cluster_weight)]:
            totals = {}
            for code, weight in weights.items():
                if mapping is None or code not in mapping:
                    return False
                group = mapping[code]
                totals[group] = totals.get(group, Decimal(0)) + weight
            if any(v > Decimal(str(limit)) + Decimal('1e-12') for v in totals.values()):
                return False
        return True

    if buffered_sells and not valid(adjusted):
        for code in buffered_sells:
            adjusted[code] = desired[code]
    demand = sum(max(Decimal(0), w-held.get(k, Decimal(0))) for k, w in adjusted.items())
    budget = Decimal(str(policy.max_buy_turnover))
    if demand > budget:
        ratio = budget / demand
        for code, weight in adjusted.items():
            previous = held.get(code, Decimal(0))
            if weight > previous:
                adjusted[code] = previous + (weight-previous)*ratio
    return {k: v for k, v in adjusted.items() if v > 0}
