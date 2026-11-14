from __future__ import annotations

import hashlib
import json
import numpy as np
import pandas as pd
from exceptions import BacktestError
from .catalogue import daily, validate_plan
from .models import BENCHMARK_NAMES


def prepare(plan, payload, returns, mask, dates):
    codes = validate_plan(plan, dates)
    spec = plan.return_benchmark
    names = dict(BENCHMARK_NAMES)
    if spec.kind == "index":
        names["index"] = codes[spec.code]["name"]
    metadata = {"name": spec.kind, "label": names[spec.kind], "definition": spec.model_dump(mode="json"),
                "source": "csmar", "return_type": "total", "market_reference": plan.market_reference,
                "rationale": plan.rationale, "index_data_hashes": {}}
    matched = spec.kind in {"same_cluster_equal_weight", "industry_equal_weight"}
    if matched:
        return pd.Series(0., index=dates), metadata
    if spec.kind == "cash":
        return pd.Series(0., index=dates), metadata
    if spec.kind == "universe_equal_weight":
        return returns.where(mask).mean(axis=1).reindex(dates).fillna(0.), metadata
    if spec.kind == "universe_float_cap_weight":
        caps = payload["market"]["circ_mv"].reindex_like(returns).shift(1).where(mask)
        if ((caps.isna() | caps.le(0)) & mask).reindex(dates).any(axis=None):
            raise BacktestError(code="BENCHMARK_CAP_MISSING", message="流通市值加权基准缺少前一交易日市值。", blocking=True)
        weights = caps.div(caps.sum(axis=1), axis=0)
        return (returns * weights).sum(axis=1).reindex(dates), metadata

    components = {spec.code: 1.} if spec.kind == "index" else {leg.code: leg.weight for leg in spec.components}
    series = {}
    for code in components:
        start = (pd.Timestamp(dates[0]) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        prices = daily(code, start, str(dates[-1].date()))
        metadata["index_data_hashes"][code] = hashlib.sha256(json.dumps(dict(prices.items()), sort_keys=True).encode()).hexdigest()
        series[code] = prices.pct_change(fill_method=None).reindex(pd.DatetimeIndex(dates).strftime("%Y-%m-%d")).to_numpy()
    frame = pd.DataFrame(series, index=dates)
    if not np.isfinite(frame.to_numpy()).all() or frame.lt(-1).any(axis=None):
        raise BacktestError(code="BENCHMARK_INDEX_RETURN_INVALID", message="CSMAR 全收益指数存在非法日收益。", blocking=True)
    if spec.kind == "index":
        return frame[spec.code], metadata
    weights = pd.Series(components, dtype=float)
    cash = spec.cash_weight
    output = []
    previous_month = None
    for date, row in frame.iterrows():
        month = date.to_period("M")
        if spec.rebalance == "daily" or month != previous_month:
            weights = pd.Series(components, dtype=float)
            cash = spec.cash_weight
        value = float((weights * row).sum())
        output.append(value)
        weights = weights * (1 + row) / (1 + value)
        cash /= 1 + value
        previous_month = month
    return pd.Series(output, index=dates), metadata
