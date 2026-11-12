from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from exceptions import PanelCacheError
from paths import workspace

from .contract import FieldCategory, FieldSpec, get_field

_MARKET = "CN-A"
_INSTRUMENT_CLASS = "stock"

@dataclass(frozen=True)
class PanelAxes:

    dates: np.ndarray  
    instruments: np.ndarray  
    window_pos_in_full: np.ndarray  
    first_trade_full_idx: np.ndarray  

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.dates), len(self.instruments))

@dataclass
class FieldBuildStats:

    name: str
    dtype: str
    nan_ratio: float = 0.0
    
    dropped_rows: int = 0
    
    events_dropped_regression: int = 0
    
    events_total: int = 0
    build_seconds: float = 0.0

def _staged_path(table: str) -> Path:
    return workspace().staged_for(_MARKET, _INSTRUMENT_CLASS) / f"{table}.parquet"

def _require_staged(table: str) -> Path:
    path = _staged_path(table)
    if not path.exists():
        from .aggregates import AGGREGATE_STAGED_TABLES

        if table in AGGREGATE_STAGED_TABLES:
            hint = "先执行 data build-aggregates 生成该派生聚合表。"
        else:
            hint = "先执行 data update 下载该表；returns_daily 需先核对字段 id。"
        raise PanelCacheError(
            code="PANEL_SOURCE_MISSING",
            message=f"staged 源表不存在：{table}（{path}）。",
            context={"table": table, "path": str(path)},
            blocking=True,
            suggested_action=hint,
        )
    return path

def staged_table_exists(table: str) -> bool:
    
    return _staged_path(table).exists()

def build_axes(start: str, end: str) -> PanelAxes:
    
    df = pd.read_parquet(_require_staged("prices_daily"), columns=["instrument_id", "date"])

    full_dates = np.sort(df["date"].unique()).astype("U10")
    lo = int(np.searchsorted(full_dates, start, side="left"))
    hi = int(np.searchsorted(full_dates, end, side="right"))
    dates = full_dates[lo:hi]
    if dates.size == 0:
        raise PanelCacheError(
            code="PANEL_EMPTY_WINDOW",
            message=f"区间 [{start}, {end}] 内 prices_daily 无任何交易日。",
            context={
                "start": start,
                "end": end,
                "staged_span": f"{full_dates[0]}~{full_dates[-1]}",
            },
            blocking=True,
            suggested_action="检查日期区间，或先执行 data update 补齐行情。",
        )

    in_window = df[(df["date"] >= dates[0]) & (df["date"] <= dates[-1])]
    instruments = np.sort(in_window["instrument_id"].unique()).astype("U6")

    first_dates = df.groupby("instrument_id")["date"].min()
    first_idx = np.searchsorted(full_dates, first_dates.reindex(instruments).to_numpy(dtype="U10"))

    return PanelAxes(
        dates=dates,
        instruments=instruments,
        window_pos_in_full=np.arange(lo, hi, dtype=np.int64),
        first_trade_full_idx=first_idx.astype(np.int64),
    )

def build_simple_field(
    axes: PanelAxes, spec: FieldSpec
) -> tuple[FieldSpec, np.ndarray, FieldBuildStats]:
    
    t0 = time.time()
    df = pd.read_parquet(
        _require_staged(spec.source_table),
        columns=["instrument_id", "date", spec.source_column],
    )
    df = df[(df["date"] >= axes.dates[0]) & (df["date"] <= axes.dates[-1])]

    row = pd.Index(axes.dates).get_indexer(df["date"].to_numpy())
    col = pd.Index(axes.instruments).get_indexer(df["instrument_id"].to_numpy())
    valid = (row >= 0) & (col >= 0)
    dropped = int((~valid).sum())

    if spec.dtype == "string":

        values = df[spec.source_column].to_numpy(dtype=object)[valid]
        values = np.where(pd.isna(values), None, values.astype(str))
        grid = np.full(axes.shape, None, dtype=object)
        grid[row[valid], col[valid]] = values
        nan_ratio = float(np.mean(np.equal(grid, None)))
    else:
        values = pd.to_numeric(df[spec.source_column], errors="coerce").to_numpy(np.float64)
        grid = np.full(axes.shape, np.nan)
        grid[row[valid], col[valid]] = values[valid]
        nan_ratio = float(np.isnan(grid).mean())

    stats = FieldBuildStats(
        name=spec.name,
        dtype=spec.dtype,
        nan_ratio=nan_ratio,
        dropped_rows=dropped,
        build_seconds=time.time() - t0,
    )
    return spec, grid, stats

def _ffill_columns_float(sparse: np.ndarray) -> np.ndarray:
    
    n_dates, n_inst = sparse.shape
    idx = np.where(~np.isnan(sparse), np.arange(n_dates, dtype=np.int32)[:, None], np.int32(-1))
    np.maximum.accumulate(idx, axis=0, out=idx)
    filled = sparse[np.maximum(idx, 0), np.arange(n_inst)[None, :]]
    filled[idx < 0] = np.nan
    return filled

def _ffill_columns_object(sparse: np.ndarray) -> np.ndarray:
    
    n_dates, n_inst = sparse.shape
    idx = np.where(
        ~np.equal(sparse, None), np.arange(n_dates, dtype=np.int32)[:, None], np.int32(-1)
    )
    np.maximum.accumulate(idx, axis=0, out=idx)
    filled = sparse[np.maximum(idx, 0), np.arange(n_inst)[None, :]]
    filled[idx < 0] = None
    return filled

def _event_grid_positions(
    axes: PanelAxes,
    effective_dates: np.ndarray,
    instrument_ids: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    
    n_dates = len(axes.dates)
    row_all = np.searchsorted(axes.dates, effective_dates, side="left")
    col_all = pd.Index(axes.instruments).get_indexer(instrument_ids)

    valid = (row_all < n_dates) & (col_all >= 0)
    dropped = int((~valid).sum())
    positions = np.nonzero(valid)[0]
    row_v, col_v = row_all[valid], col_all[valid]

    order = np.lexsort((np.arange(row_v.size), row_v, col_v))
    r_s, c_s = row_v[order], col_v[order]
    is_last = np.ones(order.size, dtype=bool)
    if order.size > 1:
        is_last[:-1] = (r_s[1:] != r_s[:-1]) | (c_s[1:] != c_s[:-1])
    sel = order[is_last]

    return row_v[sel], col_v[sel], positions[sel], dropped

def iter_fundamental_fields(
    axes: PanelAxes,
    specs: Sequence[FieldSpec],
    *,
    exclude_imputed: bool = False,
) -> Iterator[tuple[FieldSpec, np.ndarray, FieldBuildStats]]:
    
    by_table: dict[str, list[FieldSpec]] = {}
    for spec in specs:
        by_table.setdefault(spec.source_table, []).append(spec)

    for table, table_specs in by_table.items():
        value_cols = [s.source_column for s in table_specs]
        df = pd.read_parquet(
            _require_staged(table),
            columns=["instrument_id", "report_period", "statement_type", "effective_date"]
            + (["ann_date_imputed"] if exclude_imputed else [])
            + value_cols,
        )
        df = df[df["statement_type"] == "consolidated"]
        if exclude_imputed:
            
            df = df[df["ann_date_imputed"].isna()]

        df = df.sort_values(
            ["instrument_id", "effective_date", "report_period"], kind="stable"
        ).reset_index(drop=True)

        events_total = len(df)
        rp = pd.to_datetime(df["report_period"])
        keep = rp.eq(rp.groupby(df["instrument_id"], sort=False).cummax())
        events_dropped = int((~keep).sum())
        df = df[keep].reset_index(drop=True)

        rows, cols, keep_pos, dropped = _event_grid_positions(
            axes,
            df["effective_date"].to_numpy(dtype="U10"),
            df["instrument_id"].to_numpy(),
        )

        for spec in table_specs:
            t0 = time.time()
            values = pd.to_numeric(df[spec.source_column], errors="coerce").to_numpy(np.float64)
            values = values[keep_pos]
            
            values = np.where(np.isnan(values), np.inf, values)

            sparse = np.full(axes.shape, np.nan)
            sparse[rows, cols] = values
            grid = _ffill_columns_float(sparse)
            del sparse
            grid[np.isinf(grid)] = np.nan

            yield (
                spec,
                grid,
                FieldBuildStats(
                    name=spec.name,
                    dtype=spec.dtype,
                    nan_ratio=float(np.isnan(grid).mean()),
                    dropped_rows=dropped,
                    events_dropped_regression=events_dropped,
                    events_total=events_total,
                    build_seconds=time.time() - t0,
                ),
            )

def iter_aggregate_fields(
    axes: PanelAxes,
    specs: Sequence[FieldSpec],
) -> Iterator[tuple[FieldSpec, np.ndarray, FieldBuildStats]]:
    
    by_table: dict[str, list[FieldSpec]] = {}
    for spec in specs:
        by_table.setdefault(spec.source_table, []).append(spec)

    for table, table_specs in by_table.items():
        value_cols = [s.source_column for s in table_specs]
        df = pd.read_parquet(
            _require_staged(table),
            columns=["instrument_id", "effective_date"] + value_cols,
        )
        
        df = df.sort_values(["instrument_id", "effective_date"], kind="stable").reset_index(
            drop=True
        )
        events_total = len(df)

        rows, cols, keep_pos, dropped = _event_grid_positions(
            axes,
            df["effective_date"].to_numpy(dtype="U10"),
            df["instrument_id"].to_numpy(),
        )

        for spec in table_specs:
            t0 = time.time()
            values = pd.to_numeric(df[spec.source_column], errors="coerce").to_numpy(np.float64)
            values = values[keep_pos]
            
            values = np.where(np.isnan(values), np.inf, values)

            sparse = np.full(axes.shape, np.nan)
            sparse[rows, cols] = values
            grid = _ffill_columns_float(sparse)
            del sparse
            grid[np.isinf(grid)] = np.nan

            yield (
                spec,
                grid,
                FieldBuildStats(
                    name=spec.name,
                    dtype=spec.dtype,
                    nan_ratio=float(np.isnan(grid).mean()),
                    dropped_rows=dropped,
                    events_total=events_total,
                    build_seconds=time.time() - t0,
                ),
            )

def build_industry_field(
    axes: PanelAxes,
    spec: FieldSpec,
    *,
    classification_id: str,
) -> tuple[FieldSpec, np.ndarray, FieldBuildStats]:
    
    t0 = time.time()
    df = pd.read_parquet(
        _require_staged("industry_classification"),
        columns=["instrument_id", "classification_id", "effective_date", "industry_code"],
    )
    subset = df[df["classification_id"] == classification_id]
    if subset.empty:
        raise PanelCacheError(
            code="PANEL_UNKNOWN_CLASSIFICATION",
            message=f"行业分类体系 {classification_id} 在 staged 数据中不存在。",
            context={"available": sorted(df["classification_id"].unique().tolist())},
            blocking=True,
            suggested_action="用 --industry 指定 staged 中实际存在的分类体系 id。",
        )

    subset = subset.sort_values(["instrument_id", "effective_date"], kind="stable").reset_index(
        drop=True
    )
    rows, cols, keep_pos, dropped = _event_grid_positions(
        axes,
        subset["effective_date"].to_numpy(dtype="U10"),
        subset["instrument_id"].to_numpy(),
    )

    sparse = np.full(axes.shape, None, dtype=object)
    sparse[rows, cols] = subset["industry_code"].to_numpy(dtype=object)[keep_pos]
    grid = _ffill_columns_object(sparse)
    del sparse

    stats = FieldBuildStats(
        name=spec.name,
        dtype=spec.dtype,
        nan_ratio=float(np.mean(np.equal(grid, None))),
        dropped_rows=dropped,
        build_seconds=time.time() - t0,
    )
    return spec, grid, stats

def _iter_derived_fields(
    axes: PanelAxes, specs: Sequence[FieldSpec]
) -> Iterator[tuple[FieldSpec, np.ndarray, FieldBuildStats]]:
    names = {s.name for s in specs}

    if "is_trading" in names:
        t0 = time.time()
        _, close_grid, _ = build_simple_field(axes, get_field("close"))
        grid = ~np.isnan(close_grid)
        del close_grid
        yield (
            get_field("is_trading"),
            grid,
            FieldBuildStats(
                name="is_trading",
                dtype="bool",
                nan_ratio=0.0,
                build_seconds=time.time() - t0,
            ),
        )

    if "listed_days" in names:
        t0 = time.time()

        listed = axes.window_pos_in_full[:, None] - axes.first_trade_full_idx[None, :] + 1
        grid = np.clip(listed, 0, None).astype(np.int32)
        yield (
            get_field("listed_days"),
            grid,
            FieldBuildStats(
                name="listed_days",
                dtype="int32",
                nan_ratio=0.0,
                build_seconds=time.time() - t0,
            ),
        )

# 统一入口

def iter_build(
    axes: PanelAxes,
    specs: Sequence[FieldSpec],
    *,
    exclude_imputed: bool = False,
    industry_classification_id: str = "P0221",
) -> Iterator[tuple[FieldSpec, np.ndarray, FieldBuildStats]]:
    
    simple = [s for s in specs if s.category in (FieldCategory.PRICE, FieldCategory.ADJUSTED)]
    fundamental = [s for s in specs if s.category is FieldCategory.FUNDAMENTAL]
    aggregate = [s for s in specs if s.category is FieldCategory.AGGREGATE]
    industry = [s for s in specs if s.category is FieldCategory.INDUSTRY]
    derived = [s for s in specs if s.category is FieldCategory.DERIVED]

    for spec in simple:
        yield build_simple_field(axes, spec)

    yield from iter_fundamental_fields(axes, fundamental, exclude_imputed=exclude_imputed)

    yield from iter_aggregate_fields(axes, aggregate)

    for spec in industry:
        yield build_industry_field(axes, spec, classification_id=industry_classification_id)

    yield from _iter_derived_fields(axes, derived)
