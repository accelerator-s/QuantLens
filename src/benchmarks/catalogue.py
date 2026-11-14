from __future__ import annotations

import hashlib
import math
import re
import duckdb
import pandas as pd
from exceptions import ContractViolation
from paths import workspace


def staged_path(table: str):
    return workspace().staged_for("CN-A", "stock") / f"{table}.parquet"


def catalogue() -> list[dict]:
    path = staged_path("index_basic")
    if not path.exists():
        raise ContractViolation(code="BENCHMARK_CATALOGUE_MISSING", message="CSMAR 指数目录尚未更新。", blocking=True)
    frame = pd.read_parquet(path)
    frame = frame.astype(object).where(pd.notna(frame), None)
    ambiguous = set(frame.loc[frame["index_code"].duplicated(keep=False), "index_code"])
    records = []
    for row in frame.to_dict("records"):
        name = str(row["name"])
        row["return_type"] = ("net_total" if re.search(r"净收益|净回报|net.*return", name, re.I)
                              else "total" if re.search(r"全收益|全回报|总回报|total.*return", name, re.I) else "price")
        row["ambiguous_code"] = row["index_code"] in ambiguous
        row["domestic_equity"] = (not row["ambiguous_code"] and str(row.get("asset_type", "")).removesuffix(".0") == "1"
                                  and str(row.get("publisher", "")).removesuffix(".0") in {"1", "2", "3", "4"}
                                  and not re.search(r"港股|沪港深|深港通|港元|港币|香港|全球|海外|美国|美元|美股|日经|恒生", name))
        base_name = re.sub(r"(全收益|净收益)?指数$", "", name)
        row["market_eligible"] = row["domestic_equity"] and bool(
            re.fullmatch(r"(?:新?上证|深证|巨潮|国证|中证|MSCI中国)(?:全指|全A|A指|A股|综合|综指)|沪深300|中证800|中证A500", base_name))
        records.append(row)
    return records


def daily(code: str, start: str, end: str) -> pd.Series:
    path = staged_path("index_daily")
    if not path.exists():
        raise ContractViolation(code="BENCHMARK_QUOTES_MISSING", message="CSMAR 指数日行情尚未更新。", blocking=True)
    with duckdb.connect() as db:
        rows = db.execute("SELECT date,close FROM read_parquet(?) WHERE index_code=? AND date BETWEEN ? AND ? ORDER BY date",
                          [str(path), code, start, end]).fetchall()
    return pd.Series({str(day)[:10]: float(close) for day, close in rows
                      if close is not None and math.isfinite(float(close)) and float(close) > 0}, dtype=float)


def catalogue_hash() -> str:
    return hashlib.sha256(staged_path("index_basic").read_bytes()).hexdigest()


def coverage():
    path = staged_path("index_daily")
    if not path.exists():
        raise ContractViolation(code="BENCHMARK_QUOTES_MISSING", message="须先更新 CSMAR 指数日行情。")
    with duckdb.connect() as db:
        rows = db.execute("SELECT index_code,min(date),max(date) FROM read_parquet(?) WHERE close>0 AND isfinite(close) GROUP BY index_code", [str(path)]).fetchall()
    return {code: {"first_date": first, "last_date": last} for code, first, last in rows}


def validate_plan(plan, dates):
    codes = {row["index_code"]: row for row in catalogue()}
    required = [plan.market_reference]
    if plan.return_benchmark.kind == "index":
        required.append(plan.return_benchmark.code)
    required.extend(c.code for c in plan.return_benchmark.components)
    labels = pd.DatetimeIndex(dates).strftime("%Y-%m-%d")
    if labels.empty:
        raise ContractViolation(code="BENCHMARK_WINDOW_EMPTY", message="基准评价区间没有交易日。")
    for code in set(required):
        if code not in codes or not codes[code]["domestic_equity"]:
            raise ContractViolation(code="BENCHMARK_INDEX_INVALID", message=f"{code} 不在 CSMAR 境内股票指数目录中。")
        if code == plan.market_reference and not codes[code]["market_eligible"]:
            raise ContractViolation(code="BENCHMARK_MARKET_SCOPE_INVALID", message=f"{code} 不是整体股票市场参照。")
        start = (pd.Timestamp(labels[0]) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        prices = daily(code, start, labels[-1])
        prior = prices[prices.index < labels[0]]
        if prior.empty or (pd.Timestamp(labels[0]) - pd.Timestamp(prior.index[-1])).days > 15 or prices.reindex(labels).isna().any():
            raise ContractViolation(code="BENCHMARK_COVERAGE_INCOMPLETE", message=f"{code} 缺少评价区间行情或起始日前收盘价，须补齐 CSMAR 数据。")
        if code != plan.market_reference or code == plan.return_benchmark.code or any(c.code == code for c in plan.return_benchmark.components):
            if codes[code]["return_type"] != "total":
                raise ContractViolation(code="BENCHMARK_TOTAL_RETURN_REQUIRED", message=f"{code} 不是已确认的人民币税前全收益指数，不能用作收益基准。")
    return codes
