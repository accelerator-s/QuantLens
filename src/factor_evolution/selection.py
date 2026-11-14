"""Compare feasible, profitable, risk-adjusted outcomes before raw excess return."""
from __future__ import annotations

import math
import ast
import hashlib
import json
from statistics import pstdev

from .models import FactorVersion, VersionResult


def training_noninferiority(candidate: dict, parent: dict) -> bool:
    """Do not promote a lower-return candidate just because Calmar increased.

    1e-6 only accommodates the persisted metrics' six-decimal rounding; it is
    not an economic loss tolerance. Matched references remain descriptive.
    """
    from exceptions import ContractViolation
    left, right = candidate.get('research_comparison_hash'), parent.get('research_comparison_hash')
    if (left != right or candidate.get('research_input_hash') != parent.get('research_input_hash')
            or candidate.get('research_benchmark_return_hash') != parent.get('research_benchmark_return_hash')):
        raise ContractViolation(code='RESEARCH_COMPARISON_CHANGED',
            message='候选与父版本的资金、冻结基准、输入快照或交易协议不同，禁止比较。', blocking=True)
    return (float(candidate['returns']['annualized_return']) + 1e-6 >= float(parent['returns']['annualized_return'])
            and abs(float(candidate['risk']['max_drawdown'])) <= abs(float(parent['risk']['max_drawdown'])) + 1e-6)


def definition_key(version: FactorVersion) -> str:
    try:
        source = ast.dump(ast.parse(version.code), include_attributes=False)
    except SyntaxError:
        source = version.code.strip()  # Syntax validation still rejects this before execution.
    payload = {'code': source,
               'direction': version.direction, 'dependencies': sorted(version.dependencies),
               'standardization': version.standardization, 'missing_handling': version.missing_handling}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def quality_key(result: VersionResult) -> tuple | None:
    windows = [result.train, result.validation]
    if any(window is None for window in windows):
        return None
    try:
        returns = [float(window.metrics['returns']['annualized_return']) for window in windows]
        calmar = [float(window.metrics['objective']['value']) for window in windows]
        excess = float(result.score)
        folds = result.validation.metrics.get('walk_forward', {}).get('fold_values', [excess])
        dispersion = pstdev(float(value) for value in folds)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in [*returns, *calmar, excess, dispersion]):
        return None
    feasible = all(window.metrics.get('objective', {}).get('eligible') for window in windows)
    return (bool(result.constraints_passed), feasible, min(returns) > 0,
            round(min(calmar) - dispersion, 8), round(excess, 8))


def training_quality_key(result: VersionResult) -> tuple | None:
    """Rank search candidates using only the fixed training window."""
    if result.train is None or result.validation is not None or result.test is not None:
        return None
    metrics = result.train.metrics
    try:
        net = float(metrics['returns']['annualized_return'])
        excess = float(metrics['returns']['annualized_excess_return'])
        calmar = float(metrics['objective']['value'])
        drawdown = abs(float(metrics['risk']['max_drawdown']))
        turnover = float(metrics['trading']['annualized_turnover_oneside'])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (net, excess, calmar, drawdown, turnover)):
        return None
    return (bool(metrics['objective'].get('eligible')), net > 0,
            round(calmar, 8), round(excess, 8), -round(drawdown, 8), -round(turnover, 8))
