from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from exceptions import PanelCacheError
from paths import workspace

from .aggregates import (
    _QUARTER_ENDS,
    CONSENSUS_WINDOW_DAYS,
    HOLDER_STALE_DAYS,
    LONGLINE_HOLDER_TYPES,
)
from .api import _require_fresh
from .cache import frame_data_hash, load_axes, load_field
from .contract import get_field

_PIT_CHECK_FIELDS = ("total_assets", "net_profit", "cfo_net", "roe_ttm")

@dataclass
class CheckReport:

    variant: str
    samples: int
    seed: int
    passed: list[str] = field(default_factory=list)  # "校验项：通过说明"
    failures: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

def _expected_pit_value(events: pd.DataFrame, date: str, value_col: str) -> float:
    
    vis = events[events["effective_date"] <= date]
    if vis.empty:
        return float("nan")
    max_rp = vis["report_period"].max()
    return float(vis[vis["report_period"] == max_rp].iloc[-1][value_col])

def _check_pit_field(
    report: CheckReport,
    field_name: str,
    variant: str,
    exclude_imputed: bool,
    rng: np.random.Generator,
    n_samples: int,
) -> None:
    
    spec = get_field(field_name)
    frame = load_field(variant, field_name)

    staged = workspace().staged_for("CN-A", "stock") / f"{spec.source_table}.parquet"
    events = pd.read_parquet(
        staged,
        columns=[
            "instrument_id",
            "report_period",
            "statement_type",
            "effective_date",
            "ann_date_imputed",
            spec.source_column,
        ],
    )
    events = events[events["statement_type"] == "consolidated"]
    if exclude_imputed:
        events = events[events["ann_date_imputed"].isna()]
    events = events.sort_values(["effective_date", "report_period"], kind="stable")

    dates = frame.index.to_numpy()
    instruments = frame.columns.to_numpy()
    row_idx = rng.integers(0, len(dates), size=n_samples)
    col_idx = rng.integers(0, len(instruments), size=n_samples)

    grouped = dict(tuple(events.groupby("instrument_id", sort=False)))
    values = frame.to_numpy()

    mismatches: list[str] = []
    for r, c in zip(row_idx, col_idx, strict=True):
        date, inst = str(dates[r]), str(instruments[c])
        cached = float(values[r, c])
        ev = grouped.get(inst)
        expected = float("nan") if ev is None else _expected_pit_value(ev, date, spec.source_column)
        both_nan = np.isnan(cached) and np.isnan(expected)
        if not both_nan and not np.isclose(cached, expected, rtol=1e-12, atol=0.0, equal_nan=True):
            mismatches.append(f"{field_name}[{date}, {inst}]：缓存 {cached!r} ≠ 重算 {expected!r}")
            if len(mismatches) >= 5:
                break

    if mismatches:
        report.failures.extend(mismatches)
    else:
        report.passed.append(
            f"PIT 不变量（{field_name}，{variant}）：{n_samples} 个抽样格与 staged 事件重算一致"
        )

def _check_consensus_pit(report: CheckReport, rng: np.random.Generator, n_samples: int) -> None:
    
    src = workspace().staged_for("CN-A", "stock") / "analyst_forecast.parquet"
    if not src.exists():
        report.failures.append(
            "聚合 PIT（consensus）：原始源表 analyst_forecast 缺失，无法独立重算"
        )
        return
    frame_mean = load_field("default", "consensus_fy1_mean")
    frame_cov = load_field("default", "consensus_fy1_coverage")

    df = pd.read_parquet(
        src,
        columns=[
            "instrument_id",
            "publish_date",
            "forecast_year",
            "forecast_net_profit",
            "institution_id",
            "institution_name",
            "report_id",
        ],
    )
    
    df = df[
        df["publish_date"].notna() & df["forecast_net_profit"].notna() & df["forecast_year"].notna()
    ]
    key = df["institution_id"].astype("string").fillna(df["institution_name"].astype("string"))
    df = df[key.notna().to_numpy()]
    df = df.assign(
        _key=key[key.notna()].to_numpy(dtype=object),
        _fy=pd.to_numeric(df["forecast_year"], errors="coerce"),
        _pub=pd.to_datetime(df["publish_date"]).dt.normalize(),
        _rid=pd.to_numeric(df["report_id"], errors="coerce").fillna(-1.0),
    )
    df = df[df["_fy"].notna()]

    dates = frame_mean.index.to_numpy()
    instruments = frame_mean.columns.to_numpy()
    row_idx = rng.integers(0, len(dates), size=n_samples)
    col_idx = rng.integers(0, len(instruments), size=n_samples)
    grouped = dict(tuple(df.groupby("instrument_id", sort=False)))
    mean_vals = frame_mean.to_numpy()
    cov_vals = frame_cov.to_numpy()
    window = pd.Timedelta(days=CONSENSUS_WINDOW_DAYS)

    mismatches: list[str] = []
    for r, c in zip(row_idx, col_idx, strict=True):
        t = pd.Timestamp(str(dates[r]))
        inst = str(instruments[c])
        fy = t.year - 1 if t.month <= 4 else t.year
        g = grouped.get(inst)
        exp_mean, exp_cov = float("nan"), 0
        if g is not None:
            vis = g[(g["_fy"] == fy) & (g["_pub"] <= t) & (g["_pub"] + window > t)]
            if not vis.empty:
                latest = vis.sort_values(["_pub", "_rid"], kind="stable").drop_duplicates(
                    "_key", keep="last"
                )
                exp_mean = float(latest["forecast_net_profit"].astype("float64").mean())
                exp_cov = len(latest)
        cached_mean = float(mean_vals[r, c])
        cached_cov = float(cov_vals[r, c])
        mean_ok = bool(
            (np.isnan(cached_mean) and np.isnan(exp_mean))
            or np.isclose(cached_mean, exp_mean, rtol=1e-9, atol=0.0, equal_nan=True)
        )

        if exp_cov == 0:
            cov_ok = np.isnan(cached_cov) or cached_cov == 0.0
        else:
            cov_ok = cached_cov == float(exp_cov)
        if not (mean_ok and cov_ok):
            mismatches.append(
                f"consensus[{t:%Y-%m-%d}, {inst}]：缓存 mean={cached_mean!r}"
                f"/cov={cached_cov!r} ≠ 重算 mean={exp_mean!r}/cov={exp_cov}"
            )
            if len(mismatches) >= 5:
                break

    if mismatches:
        report.failures.extend(mismatches)
    else:
        report.passed.append(
            f"聚合 PIT（consensus_fy1_mean/coverage）：{n_samples} 个抽样格"
            "与 analyst_forecast 原始记录重算一致"
        )

def _check_holder_pit(report: CheckReport, rng: np.random.Generator, n_samples: int) -> None:
    
    src = workspace().staged_for("CN-A", "stock") / "top_float_shareholders.parquet"
    if not src.exists():
        report.failures.append(
            "聚合 PIT（holder）：原始源表 top_float_shareholders 缺失，无法独立重算"
        )
        return
    frame = load_field("default", "inst_longline_ratio")
    try:
        df = pd.read_parquet(
            src,
            columns=["instrument_id", "report_period", "ann_date", "holder_type", "pct_of_total"],
        )
    except Exception as exc:  
        report.failures.append(f"聚合 PIT（holder）：top_float_shareholders 读取失败（{exc}）")
        return

    df = df[df["report_period"].str[5:].isin(_QUARTER_ENDS)]
    lpct = df["pct_of_total"].where(df["holder_type"].isin(LONGLINE_HOLDER_TYPES), 0.0)
    pairs = (
        df.assign(_lpct=lpct.fillna(0.0))
        .groupby(["instrument_id", "report_period"], as_index=False)
        .agg(_ratio=("_lpct", "sum"), _ann=("ann_date", "min"))
    )
    pairs = pairs[pairs["_ann"].notna()]
    pairs = pairs.assign(_ann=pd.to_datetime(pairs["_ann"]))
    pairs = pairs.sort_values(["instrument_id", "_ann", "report_period"], kind="stable")
    by_inst = dict(tuple(pairs.groupby("instrument_id", sort=False)))

    dates = frame.index.to_numpy()
    instruments = frame.columns.to_numpy()
    row_idx = rng.integers(0, len(dates), size=n_samples)
    col_idx = rng.integers(0, len(instruments), size=n_samples)
    vals = frame.to_numpy()
    stale = pd.Timedelta(days=HOLDER_STALE_DAYS)

    mismatches: list[str] = []
    for r, c in zip(row_idx, col_idx, strict=True):
        t = pd.Timestamp(str(dates[r]))
        inst = str(instruments[c])
        g = by_inst.get(inst)
        expected = float("nan")
        if g is not None:
            vis = g[g["_ann"] <= t]
            if not vis.empty:
                
                rp = pd.to_datetime(vis["report_period"])
                last = vis[rp.eq(rp.cummax()).to_numpy()].iloc[-1]
                if t < last["_ann"] + stale:
                    expected = float(last["_ratio"])
        cached = float(vals[r, c])
        both_nan = np.isnan(cached) and np.isnan(expected)
        if not both_nan and not np.isclose(cached, expected, rtol=1e-9, atol=0.0, equal_nan=True):
            mismatches.append(
                f"inst_longline_ratio[{t:%Y-%m-%d}, {inst}]：缓存 {cached!r} ≠ 重算 {expected!r}"
            )
            if len(mismatches) >= 5:
                break

    if mismatches:
        report.failures.extend(mismatches)
    else:
        report.passed.append(
            f"聚合 PIT（inst_longline_ratio）：{n_samples} 个抽样格"
            "与 top_float_shareholders 原始记录重算一致"
        )

def run_cache_check(*, samples: int = 200, seed: int = 0) -> CheckReport:
    
    t0 = time.time()
    meta = _require_fresh("default")
    report = CheckReport(variant="default", samples=samples, seed=seed)
    rng = np.random.default_rng(seed)

    cached_fields = list((meta.get("fields") or {}).keys())
    dates_axis, instruments_axis = load_axes("default")

    probe = sorted({cached_fields[0], cached_fields[len(cached_fields) // 2], cached_fields[-1]})
    for name in probe:
        frame = load_field("default", name)
        if not frame.index.equals(dates_axis) or not frame.columns.equals(instruments_axis):
            report.failures.append(f"轴一致：字段 {name} 的 index/columns 与 _axes 落盘轴不一致")
            continue
        recorded = meta["fields"][name]["data_hash"]
        actual = frame_data_hash(frame, meta["fields"][name]["dtype"])
        if actual != recorded:
            report.failures.append(
                f"哈希回读：字段 {name} 回读哈希 {actual[:8]} ≠ meta 记录 {recorded[:8]}"
            )
    if not any(f.startswith(("轴一致", "哈希回读")) for f in report.failures):
        report.passed.append(f"轴一致 + 哈希回读：抽样字段 {'、'.join(probe)} 全部通过")

    for name in _PIT_CHECK_FIELDS:
        if name in cached_fields:
            _check_pit_field(report, name, "default", False, rng, samples)

    from .cache import status as cache_state

    if cache_state("strict").state == "fresh":
        strict_fields = list((_require_fresh("strict").get("fields") or {}).keys())
        for name in _PIT_CHECK_FIELDS:
            if name in strict_fields:
                _check_pit_field(report, name, "strict", True, rng, samples)

    if "listed_days" in cached_fields:
        listed = load_field("default", "listed_days").to_numpy()
        if (np.diff(listed.astype(np.int64), axis=0) < 0).any():
            report.failures.append("listed_days：存在沿日期轴递减的列")
        elif (listed < 0).any():
            report.failures.append("listed_days：存在负值")
        else:
            report.passed.append("listed_days：全列沿日期轴单调不减且非负")

    if "consensus_fy1_mean" in cached_fields and "consensus_fy1_coverage" in cached_fields:
        _check_consensus_pit(report, rng, samples)
    if "inst_longline_ratio" in cached_fields:
        _check_holder_pit(report, rng, samples)

    report.duration_seconds = time.time() - t0

    if report.failures:
        raise PanelCacheError(
            code="PANEL_CHECK_FAILED",
            message=f"缓存抽样校验失败：{len(report.failures)} 项不通过。",
            context={"failures": report.failures, "passed": report.passed},
            blocking=True,
            can_open_issue=True,
            suggested_action=(
                "核查 staged 源数据与缓存构建逻辑；确认非并发更新所致后，"
                "创建 data_quality_conflict 或 bug issue。"
            ),
        )
    return report
