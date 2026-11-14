"""Render the frozen market reference from CSMAR only, without network fetching."""
from __future__ import annotations

import hashlib
import json
import pandas as pd
from .catalogue import catalogue, daily
from .models import MarketReference, ReferenceComparison


def comparison(reference: MarketReference, nav, initial_capital):
    records = {r["index_code"]: r for r in catalogue()}
    record = records[reference.code]
    result = {"code": reference.code, "name": record["name"], "source": "csmar",
              "return_type": record["return_type"], "selection_timing": reference.selection_timing}
    def response(**values):
        return ReferenceComparison(**result, **values).model_dump(mode='json')
    if nav.empty:
        return response(status='unavailable', message='尚无回测净值。')
    dates = nav["date"].astype(str).str[:10]
    start = (pd.Timestamp(dates.iloc[0]) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    prices = daily(reference.code, start, dates.iloc[-1])
    prior = prices[prices.index < dates.iloc[0]]
    if prior.empty or (pd.Timestamp(dates.iloc[0]) - pd.Timestamp(prior.index[-1])).days > 15:
        return response(status='unavailable', message='CSMAR 缺少起始日前的指数收盘价，请更新数据。')
    aligned = prices.reindex(dates) / float(prior.iloc[-1])
    valid = aligned.notna() & aligned.gt(0)
    missing = dates[~valid.to_numpy()].tolist()
    total = None if missing else float(aligned.iloc[-1] - 1)
    excess = float(nav["nav"].iloc[-1]) / float(initial_capital) - 1 - total if initial_capital and total is not None else None
    return response(status='partial' if missing else 'available', total_return=total,
            excess_return=excess, missing_dates=missing,
            message=f'CSMAR 缺少 {len(missing)} 个交易日的指数行情。' if missing else '',
            data_hash=hashlib.sha256(json.dumps(dict(prices.items()), sort_keys=True).encode()).hexdigest(),
            items=[{'date': day, 'nav': float(value) if ok else None} for day, value, ok in zip(dates, aligned, valid)])
