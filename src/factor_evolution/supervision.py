"""Fail-closed checks used before spending time on a factor backtest.

These checks detect common forms of look-ahead; they do not certify the source
data's point-in-time provenance or establish that a strategy generalizes.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping

import numpy as np
import pandas as pd

from exceptions import ContractViolation
from factors.sandbox import execute_factor


def _reject(message: str) -> None:
    raise ContractViolation(code="EVOLUTION_CAUSALITY_FAILED", message=message,
                            blocking=True)


def check_source(source: str) -> None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        keywords = {arg.arg: arg.value for arg in node.keywords}
        if method in {"bfill", "backfill"}:
            _reject("因子包含向后填充，可能把未来观测写入历史。")
        if method == "rolling" and isinstance(keywords.get("center"), ast.Constant):
            if keywords["center"].value:
                _reject("因子包含居中滚动窗口。")
        if method in {"shift", "diff", "pct_change"}:
            periods = keywords.get("periods", node.args[0] if node.args else None)
            try:
                value = ast.literal_eval(periods) if periods is not None else 1
            except (ValueError, TypeError):
                continue  # Dynamic expressions are checked on actual panels below.
            if isinstance(value, (float, int)) and value < 0:
                _reject(f"因子包含 {method}({value})，会引用未来观测。")


def audit_factor(source: str, panel: Mapping[str, pd.DataFrame], *,
                 min_history: int, cut_positions: list[int] | None = None) -> dict:
    """Check historical invariance using a bounded, real cross section.

    Use every instrument in the chosen dates, including ineligible instruments;
    sampling instruments can change a cross-sectional pool or ranking.
    """
    check_source(source)
    if not panel:
        _reject("监督面板为空。")
    template = next(iter(panel.values()))
    if template.index.has_duplicates or not template.index.is_monotonic_increasing:
        _reject("监督面板日期必须严格递增且唯一。")
    if template.columns.has_duplicates:
        _reject("监督面板证券代码重复。")
    if any(not value.index.equals(template.index) or not value.columns.equals(template.columns)
           for value in panel.values()):
        _reject("依赖字段的日期与证券轴不一致。")
    def run(inputs):
        copied = {name: frame.copy(deep=True) for name, frame in inputs.items()}
        result = execute_factor(source, copied, check_inputs=True)
        expected = next(iter(inputs.values()))
        if not isinstance(result, pd.DataFrame):
            _reject("因子输出不是 DataFrame。")
        if not result.index.equals(expected.index) or not result.columns.equals(expected.columns):
            _reject("因子输出改变了日期或证券轴。")
        try:
            array = result.to_numpy(dtype=float)
        except (TypeError, ValueError):
            _reject("因子输出包含非数值。")
        if np.isinf(array).any():
            _reject("因子输出含有无穷值。")
        for name, frame in inputs.items():
            if not copied[name].equals(frame):
                _reject(f"因子修改了输入字段 {name}。")
        return result

    full = run(panel)

    def equal(left, right, label):
        try:
            pd.testing.assert_frame_equal(left, right, check_exact=False,
                                          atol=1e-10, rtol=1e-10)
        except AssertionError as exc:
            _reject(f"{label}检查失败：{str(exc)[:240]}")

    equal(full, run(panel), "重复计算确定性")
    n = len(template)
    cuts = cut_positions if cut_positions is not None else sorted({
        max(min_history + 2, n // 2), max(min_history + 3, n * 3 // 4), n - 2})
    if not cuts or any(cut <= min_history or cut >= n for cut in cuts):
        _reject("监督面板不足以覆盖预热期和未来截断点。")
    for cut in cuts:
        prefix = {name: frame.iloc[:cut] for name, frame in panel.items()}
        equal(full.iloc[:cut], run(prefix), f"截至 {template.index[cut-1]} 的未来截断")
    # Production Spark jobs independently compute overlapping history windows.
    start = n // 4
    if n - start <= min_history:
        _reject("监督面板不足以检查分块预热。")
    chunk = run({name: frame.iloc[start:] for name, frame in panel.items()})
    equal(full.iloc[start + min_history:], chunk.iloc[min_history:], "分块预热一致性")
    mature = full.iloc[min_history:]
    if not mature.notna().any(axis=None):
        _reject("预热后的监督面板没有有效因子值。")
    return {"passed": True, "dates": n, "instruments": len(template.columns),
            "prefix_checks": len(cuts), "deterministic": True,
            "chunk_invariant": True, "input_unchanged": True,
            "mean_valid_instruments": float(mature.notna().sum(axis=1).mean())}
