from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from exceptions import PanelCacheError
from paths import workspace

_MARKET = "CN-A"
_INSTRUMENT_CLASS = "stock"

AGGREGATE_STAGED_TABLES: dict[str, tuple[str, ...]] = {
    "consensus_daily": ("analyst_forecast",),
    "holder_longline": ("top_float_shareholders",),
    "fundamental_ttm": ("fs_income", "fs_balance"),
}

CONSENSUS_WINDOW_DAYS = 180

HOLDER_STALE_DAYS = 200

LONGLINE_HOLDER_TYPES = frozenset(
    {"social_security", "mutual_fund", "insurance", "qfii", "hk_connect"}
)

# 合法季末报告期（月-日）
_QUARTER_ENDS = ("03-31", "06-30", "09-30", "12-31")

TTM_IMPUTED_MARKER = "component_imputed"

@dataclass
class AggregateBuildReport:

    table: str
    rows: int = 0
    instruments: int = 0
    source_rows: int = 0
    dropped_rows: int = 0  
    details: dict[str, int | str] = field(default_factory=dict)
    duration_seconds: float = 0.0

def _staged_dir() -> Path:
    return workspace().staged_for(_MARKET, _INSTRUMENT_CLASS)

def _require_source(table: str) -> Path:
    path = _staged_dir() / f"{table}.parquet"
    if not path.exists():
        raise PanelCacheError(
            code="PANEL_SOURCE_MISSING",
            message=f"派生聚合所需的 staged 源表不存在：{table}（{path}）。",
            context={"table": table, "path": str(path)},
            blocking=True,
            suggested_action="先执行 data update 下载该表（机构持仓表还需 data enrich-holders）。",
        )
    return path

def aggregate_source_ready(table: str) -> bool:
    
    return all(
        (_staged_dir() / f"{src}.parquet").exists() for src in AGGREGATE_STAGED_TABLES[table]
    )

def fy1_year_of(dates: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    
    dt = pd.DatetimeIndex(dates)
    return np.where(dt.month <= 4, dt.year - 1, dt.year).astype(np.int64)

def _consensus_segment_events(
    pubs: np.ndarray,  
    keys: np.ndarray,  
    vals: np.ndarray,  
    fy: int,
) -> list[tuple[np.datetime64, float, float, int]]:
    
    seg_start = np.datetime64(f"{fy}-05-01")
    seg_end = np.datetime64(f"{fy + 1}-05-01")  # 开区间
    window = np.timedelta64(CONSENSUS_WINDOW_DAYS, "D")

    n = pubs.size
    
    ev_dates = np.concatenate([pubs, pubs + window])
    ev_kind = np.concatenate([np.zeros(n, dtype=np.int8), np.ones(n, dtype=np.int8)])
    ev_idx = np.concatenate([np.arange(n), np.arange(n)])
    order = np.lexsort((ev_kind, ev_dates))

    state: dict[object, tuple[int, float]] = {}

    def _snapshot(date: np.datetime64) -> tuple[np.datetime64, float, float, int]:
        if not state:
            return (date, float("nan"), float("nan"), 0)
        arr = np.fromiter((v for _, v in state.values()), dtype=np.float64, count=len(state))
        return (date, float(arr.mean()), float(np.median(arr)), len(state))

    out: list[tuple[np.datetime64, float, float, int]] = []
    boundary_done = False
    for pos in order:
        d = ev_dates[pos]
        if d >= seg_end:
            break
        if not boundary_done and d >= seg_start:
            
            out.append(_snapshot(seg_start))
            boundary_done = True
        i = int(ev_idx[pos])
        k = keys[i]
        if ev_kind[pos] == 0:  
            state[k] = (i, float(vals[i]))
        else:  
            cur = state.get(k)
            if cur is not None and cur[0] == i:
                del state[k]
        if d >= seg_start:
            out.append(_snapshot(d))
    if not boundary_done:
        out.append(_snapshot(seg_start))

    dedup: dict[np.datetime64, tuple[np.datetime64, float, float, int]] = {}
    for ev in out:
        dedup[ev[0]] = ev
    return sorted(dedup.values(), key=lambda ev: ev[0])

def build_consensus_daily() -> AggregateBuildReport:
    
    t0 = time.time()
    df = pd.read_parquet(
        _require_source("analyst_forecast"),
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
    report = AggregateBuildReport(table="consensus_daily", source_rows=len(df))

    n0 = len(df)
    df = df[df["publish_date"].notna() & df["forecast_net_profit"].notna()]
    df = df[df["forecast_year"].notna()]
    report.details["dropped_missing_key_fields"] = n0 - len(df)

    inst_key = df["institution_id"].astype("string")
    inst_key = inst_key.fillna(df["institution_name"].astype("string"))
    n1 = len(df)
    keep = inst_key.notna()
    df = df[keep]
    inst_key = inst_key[keep]
    report.details["dropped_unidentifiable_institution"] = n1 - len(df)
    report.dropped_rows = n0 - len(df)

    df = df.assign(
        _key=inst_key.to_numpy(dtype=object),
        _fy=pd.to_numeric(df["forecast_year"], errors="coerce").astype("Int64"),
        _pub=pd.to_datetime(df["publish_date"]).to_numpy().astype("datetime64[D]"),
        _rid=pd.to_numeric(df["report_id"], errors="coerce").fillna(-1.0),
    )
    n2 = len(df)
    df = df[df["_fy"].notna()]
    report.details["dropped_unparsable_year"] = n2 - len(df)
    report.dropped_rows += n2 - len(df)

    df = df.sort_values(["instrument_id", "_fy", "_pub", "_rid"], kind="stable")

    frames: list[pd.DataFrame] = []
    n_events = 0
    for (inst, fy), g in df.groupby(["instrument_id", "_fy"], sort=True):
        events = _consensus_segment_events(

            g["_pub"].to_numpy().astype("datetime64[D]"),
            g["_key"].to_numpy(),
            g["forecast_net_profit"].to_numpy(dtype=np.float64),
            int(fy),
        )
        n_events += len(events)
        frames.append(
            pd.DataFrame(
                {
                    "instrument_id": inst,
                    "effective_date": [str(ev[0]) for ev in events],
                    "consensus_fy1_mean": [ev[1] for ev in events],
                    "consensus_fy1_median": [ev[2] for ev in events],
                    "consensus_fy1_coverage": [float(ev[3]) for ev in events],
                    "consensus_fy1_year": float(int(fy)),
                }
            )
        )

    if not frames:
        raise PanelCacheError(
            code="PANEL_AGGREGATE_EMPTY",
            message="analyst_forecast 过滤后无任何可聚合记录，consensus_daily 未生成。",
            blocking=True,
            suggested_action="检查 analyst_forecast 下载完整性与 enrich-analyst 是否执行。",
        )

    out = pd.concat(frames, ignore_index=True)

    seg_keys = out[["instrument_id", "consensus_fy1_year"]].drop_duplicates()
    cut = pd.DataFrame(
        {
            "instrument_id": seg_keys["instrument_id"].to_numpy(),
            "effective_date": [
                f"{int(y) + 1}-05-01" for y in seg_keys["consensus_fy1_year"].to_numpy()
            ],
            "consensus_fy1_mean": np.nan,
            "consensus_fy1_median": np.nan,
            "consensus_fy1_coverage": 0.0,
            "consensus_fy1_year": seg_keys["consensus_fy1_year"].to_numpy() + 1.0,
        }
    )
    existing = set(zip(out["instrument_id"], out["consensus_fy1_year"], strict=True))
    cut_needed = [
        (inst, y) not in existing
        for inst, y in zip(cut["instrument_id"], cut["consensus_fy1_year"], strict=True)
    ]
    out = pd.concat([out, cut[cut_needed]], ignore_index=True)

    out = out.sort_values(
        ["instrument_id", "effective_date", "consensus_fy1_year"], kind="stable"
    ).reset_index(drop=True)

    out.to_parquet(_staged_dir() / "consensus_daily.parquet", index=False)
    report.rows = len(out)
    report.instruments = int(out["instrument_id"].nunique())
    report.details["events_from_sweep"] = n_events
    report.details["rollover_cut_events"] = int(np.sum(cut_needed))
    report.duration_seconds = time.time() - t0
    return report

def build_holder_longline() -> AggregateBuildReport:
    
    t0 = time.time()
    path = _require_source("top_float_shareholders")
    df = pd.read_parquet(path)
    required = {"instrument_id", "report_period", "ann_date", "holder_type", "pct_of_total"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise PanelCacheError(
            code="PANEL_AGGREGATE_SOURCE_UNENRICHED",
            message=f"top_float_shareholders 缺少列 {missing}，须先关联披露日并分类股东类型。",
            context={"missing_columns": missing},
            blocking=True,
            suggested_action="先执行 data enrich-holders 后重试 data build-aggregates。",
        )
    report = AggregateBuildReport(table="holder_longline", source_rows=len(df))

    n0 = len(df)
    df = df[df["report_period"].str[5:].isin(_QUARTER_ENDS)]
    report.details["dropped_non_quarter_end"] = n0 - len(df)

    is_longline = df["holder_type"].isin(LONGLINE_HOLDER_TYPES)
    report.details["longline_rows_missing_pct"] = int(
        (is_longline & df["pct_of_total"].isna()).sum()
    )

    longline_pct = df["pct_of_total"].where(is_longline, 0.0)
    grouped = (
        df.assign(_lpct=longline_pct.fillna(0.0))
        .groupby(["instrument_id", "report_period"], as_index=False)
        .agg(inst_longline_ratio=("_lpct", "sum"), ann_date=("ann_date", "min"))
    )

    n1 = len(grouped)
    grouped = grouped[grouped["ann_date"].notna()]
    report.details["dropped_missing_ann_date_pairs"] = n1 - len(grouped)
    report.dropped_rows = (n0 - len(df)) + (n1 - len(grouped))

    grouped = grouped.sort_values(
        ["instrument_id", "ann_date", "report_period"], kind="stable"
    ).reset_index(drop=True)
    rp = pd.to_datetime(grouped["report_period"])
    keep = rp.eq(rp.groupby(grouped["instrument_id"], sort=False).cummax())
    report.details["dropped_regression_pairs"] = int((~keep).sum())
    grouped = grouped[keep].reset_index(drop=True)

    q_ord = pd.PeriodIndex(pd.to_datetime(grouped["report_period"]), freq="Q").asi8
    same_inst = grouped["instrument_id"].eq(grouped["instrument_id"].shift(1)).to_numpy()
    consecutive = same_inst & ((q_ord - np.roll(q_ord, 1)) == 1)
    chg = grouped["inst_longline_ratio"] - grouped["inst_longline_ratio"].shift(1)
    grouped["inst_longline_chg"] = chg.where(consecutive)
    report.details["chg_available_pairs"] = int(grouped["inst_longline_chg"].notna().sum())

    ann = pd.to_datetime(grouped["ann_date"])
    next_ann = ann.shift(-1).where(grouped["instrument_id"].eq(grouped["instrument_id"].shift(-1)))
    stale_at = ann + pd.Timedelta(days=HOLDER_STALE_DAYS)
    need_stale = next_ann.isna() | (next_ann > stale_at)
    stale = pd.DataFrame(
        {
            "instrument_id": grouped.loc[need_stale, "instrument_id"].to_numpy(),
            "report_period": pd.NA,
            "effective_date": stale_at[need_stale].dt.strftime("%Y-%m-%d").to_numpy(),
            "inst_longline_ratio": np.nan,
            "inst_longline_chg": np.nan,
        }
    )
    report.details["staleness_events"] = len(stale)

    events = grouped.rename(columns={"ann_date": "effective_date"})[
        [
            "instrument_id",
            "report_period",
            "effective_date",
            "inst_longline_ratio",
            "inst_longline_chg",
        ]
    ]
    out = pd.concat([events, stale], ignore_index=True)

    out = out.sort_values(
        ["instrument_id", "effective_date", "report_period"],
        kind="stable",
        na_position="first",
    ).reset_index(drop=True)

    out.to_parquet(_staged_dir() / "holder_longline.parquet", index=False)
    report.rows = len(out)
    report.instruments = int(out["instrument_id"].nunique())
    report.duration_seconds = time.time() - t0
    return report

def _shift_period(period: pd.Series, *, to_prev_annual: bool) -> pd.Series:
    
    year = (period.str[:4].astype(int) - 1).astype(str)
    suffix = "12-31" if to_prev_annual else period.str[5:]
    
    return year + "-" + suffix

def build_fundamental_ttm() -> AggregateBuildReport:
    
    t0 = time.time()
    inc = pd.read_parquet(
        _require_source("fs_income"),
        columns=[
            "instrument_id",
            "report_period",
            "statement_type",
            "effective_date",
            "ann_date_imputed",
            "net_profit_parent",
            "revenue",
            "operating_cost",
        ],
    )
    bal = pd.read_parquet(
        _require_source("fs_balance"),
        columns=[
            "instrument_id",
            "report_period",
            "statement_type",
            "effective_date",
            "ann_date_imputed",
            "equity_parent",
        ],
    )
    report = AggregateBuildReport(table="fundamental_ttm", source_rows=len(inc) + len(bal))

    inc = inc[inc["statement_type"] == "consolidated"].drop(columns=["statement_type"])
    bal = bal[bal["statement_type"] == "consolidated"].drop(columns=["statement_type"])
    n0 = len(inc)
    inc = inc[inc["report_period"].str[5:].isin(_QUARTER_ENDS)]
    bal = bal[bal["report_period"].str[5:].isin(_QUARTER_ENDS)]
    report.details["dropped_non_quarter_end_income"] = n0 - len(inc)
    
    inc = inc.drop_duplicates(["instrument_id", "report_period"], keep="last")
    bal = bal.drop_duplicates(["instrument_id", "report_period"], keep="last")

    base = inc.rename(
        columns={
            "effective_date": "eff_p",
            "ann_date_imputed": "imp_p",
            "net_profit_parent": "np_p",
            "revenue": "rev_p",
            "operating_cost": "cost_p",
        }
    )
    base["_pa"] = _shift_period(base["report_period"], to_prev_annual=True)
    base["_ps"] = _shift_period(base["report_period"], to_prev_annual=False)

    comp = inc.rename(
        columns={
            "effective_date": "eff_c",
            "ann_date_imputed": "imp_c",
            "net_profit_parent": "np_c",
            "revenue": "rev_c",
            "operating_cost": "cost_c",
        }
    )
    base = base.merge(
        comp.rename(columns=lambda c: c.replace("_c", "_pa") if c.endswith("_c") else c),
        left_on=["instrument_id", "_pa"],
        right_on=["instrument_id", "report_period"],
        how="left",
        suffixes=("", "_dup_pa"),
    ).drop(columns=["report_period_dup_pa"])
    base = base.merge(
        comp.rename(columns=lambda c: c.replace("_c", "_ps") if c.endswith("_c") else c),
        left_on=["instrument_id", "_ps"],
        right_on=["instrument_id", "report_period"],
        how="left",
        suffixes=("", "_dup_ps"),
    ).drop(columns=["report_period_dup_ps"])
    base = base.merge(
        bal.rename(
            columns={
                "effective_date": "eff_b",
                "ann_date_imputed": "imp_b",
                "equity_parent": "equity_p",
            }
        ),
        on=["instrument_id", "report_period"],
        how="left",
    )

    is_q4 = base["report_period"].str[5:] == "12-31"

    def _ttm(col: str) -> pd.Series:
        ytd = base[f"{col}_p"]
        rolled = ytd + base[f"{col}_pa"] - base[f"{col}_ps"]
        return ytd.where(is_q4, rolled)

    np_ttm = _ttm("np")
    rev_ttm = _ttm("rev")
    cost_ttm = _ttm("cost")

    equity = base["equity_p"]
    roe_ttm = (np_ttm / equity.where(equity > 0)).astype("float64")
    gross_margin_ttm = ((rev_ttm - cost_ttm) / rev_ttm.where(rev_ttm > 0)).astype("float64")

    eff_dt = pd.DataFrame(
        {
            c: pd.to_datetime(base[c], errors="coerce")
            for c in ("eff_p", "eff_pa", "eff_ps", "eff_b")
        }
    )
    eff_dt.loc[is_q4.to_numpy(), ["eff_pa", "eff_ps"]] = pd.NaT
    effective = eff_dt.max(axis=1).dt.strftime("%Y-%m-%d")
    computable = np_ttm.notna() | rev_ttm.notna()
    effective = effective.where(computable, base["eff_p"])

    imp_cols = base[["imp_p", "imp_pa", "imp_ps", "imp_b"]].copy()
    imp_cols.loc[is_q4, ["imp_pa", "imp_ps"]] = pd.NA
    any_imputed = imp_cols.notna().any(axis=1)

    out = pd.DataFrame(
        {
            "instrument_id": base["instrument_id"],
            "report_period": base["report_period"],
            "statement_type": "consolidated",
            "effective_date": effective,
            "ann_date_imputed": pd.Series(pd.NA, index=base.index, dtype="object").mask(
                any_imputed, TTM_IMPUTED_MARKER
            ),
            "net_profit_ttm_parent": np_ttm,
            "roe_ttm": roe_ttm,
            "gross_margin_ttm": gross_margin_ttm,
        }
    )
    n1 = len(out)
    out = out[out["effective_date"].notna()]
    report.dropped_rows = n1 - len(out)
    report.details["dropped_missing_effective_date"] = n1 - len(out)
    report.details["rows_ttm_computable"] = int(computable.sum())
    report.details["rows_roe_available"] = int(out["roe_ttm"].notna().sum())
    report.details["rows_imputed_component"] = int(any_imputed.sum())

    out = out.sort_values(
        ["instrument_id", "effective_date", "report_period"], kind="stable"
    ).reset_index(drop=True)
    out.to_parquet(_staged_dir() / "fundamental_ttm.parquet", index=False)
    report.rows = len(out)
    report.instruments = int(out["instrument_id"].nunique())
    report.duration_seconds = time.time() - t0
    return report

_BUILDERS = {
    "consensus_daily": build_consensus_daily,
    "holder_longline": build_holder_longline,
    "fundamental_ttm": build_fundamental_ttm,
}

def build_aggregate_table(table: str) -> AggregateBuildReport:
    
    if table not in _BUILDERS:
        raise PanelCacheError(
            code="PANEL_UNKNOWN_AGGREGATE",
            message=f"未知派生聚合表：{table}。",
            context={"known_tables": sorted(_BUILDERS)},
            blocking=True,
            suggested_action="合法表名见 data build-aggregates --help。",
        )
    return _BUILDERS[table]()

__all__ = [
    "AGGREGATE_STAGED_TABLES",
    "CONSENSUS_WINDOW_DAYS",
    "HOLDER_STALE_DAYS",
    "LONGLINE_HOLDER_TYPES",
    "TTM_IMPUTED_MARKER",
    "AggregateBuildReport",
    "aggregate_source_ready",
    "build_aggregate_table",
    "build_consensus_daily",
    "build_fundamental_ttm",
    "build_holder_longline",
    "fy1_year_of",
]
