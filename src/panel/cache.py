from __future__ import annotations

import hashlib
import os
import resource
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from exceptions import PanelCacheError
from paths import workspace

from .builder import FieldBuildStats, PanelAxes, build_axes, iter_build
from .contract import FieldCategory, FieldSpec

_MARKET = "CN-A"
_INSTRUMENT_CLASS = "stock"

SCHEMA_VERSION = 1
VARIANTS = ("default", "strict")

def _variant_dir(variant: str) -> Path:
    if variant not in VARIANTS:
        raise PanelCacheError(
            code="PANEL_UNKNOWN_VARIANT",
            message=f"未知缓存变体：{variant}。",
            context={"known_variants": list(VARIANTS)},
            blocking=True,
        )
    return workspace().matrix_cache_for(_MARKET, _INSTRUMENT_CLASS) / variant

# 源表指纹

def _source_fingerprint(table: str, manifest: Any = None) -> dict[str, Any]:
    
    import pyarrow.parquet as pq

    if manifest is None:
        from providers.markets.cn_a.csmar.manifest import read_manifest

        manifest = read_manifest()

    path = workspace().staged_for(_MARKET, _INSTRUMENT_CLASS) / f"{table}.parquet"
    stat = path.stat()
    return {
        "rows": int(pq.read_metadata(path).num_rows),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "manifest_last_date": manifest.last_date_for(table),
    }

def _source_tables(specs: list[FieldSpec]) -> list[str]:
    
    tables = {"prices_daily"}
    tables.update(s.source_table for s in specs if s.source_table != "derived")
    return sorted(tables)

def collect_fingerprints(tables: list[str]) -> dict[str, dict[str, Any]]:
    
    from providers.markets.cn_a.csmar.manifest import read_manifest

    from .builder import _require_staged

    manifest = read_manifest()
    result = {}
    for table in tables:
        _require_staged(table)
        result[table] = _source_fingerprint(table, manifest)
    return result

# 规范形哈希

def hash_axis(values: np.ndarray) -> str:
    
    h = hashlib.sha256()
    h.update("\n".join(str(v) for v in values).encode("utf-8"))
    return h.hexdigest()

def hash_grid(grid: np.ndarray, dtype: str) -> str:
    
    h = hashlib.sha256()
    if dtype == "float64":
        arr = np.asarray(grid, dtype=np.float64)
        mask = np.isnan(arr)
        arr = np.where(mask, 0.0, arr)
        arr = np.where(arr == 0.0, 0.0, arr)  
        h.update(arr.tobytes())
        h.update(np.packbits(mask).tobytes())
    elif dtype == "bool":
        h.update(np.packbits(np.asarray(grid, dtype=bool)).tobytes())
    elif dtype == "int32":
        h.update(np.asarray(grid, dtype=np.int32).tobytes())
    elif dtype == "string":
        codes, uniques = pd.factorize(np.asarray(grid, dtype=object).ravel())
        h.update(codes.astype(np.int32).tobytes())
        h.update("\x1f".join("" if pd.isna(u) else str(u) for u in uniques).encode("utf-8"))
    else:  
        raise PanelCacheError(
            code="PANEL_UNSUPPORTED_DTYPE",
            message=f"未支持的网格 dtype：{dtype}。",
            blocking=True,
        )
    return h.hexdigest()

def frame_data_hash(frame: pd.DataFrame, dtype: str) -> str:
    
    return hash_grid(frame.to_numpy(), dtype)

# 构建与落盘

@dataclass
class BuildReport:

    variant: str
    start: str
    end: str
    n_dates: int
    n_instruments: int
    field_stats: list[FieldBuildStats] = field(default_factory=list)
    field_hashes: dict[str, str] = field(default_factory=dict)
    
    all_nan_instruments: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    maxrss_mb: float = 0.0

def _grid_to_frame(grid: np.ndarray, axes: PanelAxes, dtype: str) -> pd.DataFrame:
    index = pd.Index(axes.dates.astype(str), name="date")
    columns = pd.Index(axes.instruments.astype(str))
    if dtype == "bool":
        return pd.DataFrame(grid.astype(bool), index=index, columns=columns)
    if dtype == "int32":
        return pd.DataFrame(grid.astype(np.int32), index=index, columns=columns)
    return pd.DataFrame(grid, index=index, columns=columns)

def _count_all_nan_columns(grid: np.ndarray, dtype: str) -> int:
    if dtype == "float64":
        return int(np.isnan(grid).all(axis=0).sum())
    if dtype == "string":
        return int(np.equal(grid, None).all(axis=0).sum())
    return 0

def _atomic_write_yaml(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)

def specs_for_variant(variant: str, specs: list[FieldSpec]) -> list[FieldSpec]:
    
    if variant == "strict":
        non_fund = [s.name for s in specs if s.category is not FieldCategory.FUNDAMENTAL]
        if non_fund:
            raise PanelCacheError(
                code="PANEL_VARIANT_FIELD",
                message=f"strict 变体只落盘 fundamental 字段，不接受：{non_fund}。",
                context={"invalid_fields": non_fund},
                blocking=True,
                suggested_action="非基本面字段请构建/读取 default 变体。",
            )
    return specs

def rebuild(
    variant: str,
    specs: list[FieldSpec],
    *,
    start: str,
    end: str,
    industry_classification_id: str = "P0221",
    on_field=None,
) -> BuildReport:
    
    specs = specs_for_variant(variant, specs)
    exclude_imputed = variant == "strict"
    tables = _source_tables(specs)

    t0 = time.time()
    fp_before = collect_fingerprints(tables)
    axes = build_axes(start, end)

    final_dir = _variant_dir(variant)
    tmp_dir = final_dir.parent / f"{variant}.building-{os.getpid()}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    (tmp_dir / "_axes").mkdir(parents=True)
    (tmp_dir / "fields").mkdir()

    report = BuildReport(
        variant=variant,
        start=start,
        end=end,
        n_dates=len(axes.dates),
        n_instruments=len(axes.instruments),
    )

    try:
        pd.DataFrame({"date": axes.dates.astype(str)}).to_parquet(
            tmp_dir / "_axes" / "dates.parquet", index=False
        )
        pd.DataFrame({"instrument_id": axes.instruments.astype(str)}).to_parquet(
            tmp_dir / "_axes" / "instruments.parquet", index=False
        )

        for spec, grid, stats in iter_build(
            axes,
            specs,
            exclude_imputed=exclude_imputed,
            industry_classification_id=industry_classification_id,
        ):
            frame = _grid_to_frame(grid, axes, spec.dtype)
            frame.to_parquet(tmp_dir / "fields" / f"{spec.name}.parquet")
            report.field_hashes[spec.name] = hash_grid(grid, spec.dtype)
            report.all_nan_instruments[spec.name] = _count_all_nan_columns(grid, spec.dtype)
            report.field_stats.append(stats)
            del grid, frame
            if on_field is not None:
                on_field(spec, stats)

        fp_after = collect_fingerprints(tables)
        if fp_after != fp_before:
            changed = sorted(t for t in tables if fp_before[t] != fp_after[t])
            raise PanelCacheError(
                code="PANEL_CACHE_STALE",
                message=f"构建期间源表发生变更：{changed}。缓存未提交。",
                context={"changed_tables": changed},
                blocking=True,
                suggested_action="等待 data update 完成后重新执行 data rebuild-cache。",
            )

        report.duration_seconds = time.time() - t0
        report.maxrss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

        meta = {
            "schema_version": SCHEMA_VERSION,
            "variant": variant,
            "built_at": datetime.now(tz=UTC).isoformat(),
            "duration_seconds": round(report.duration_seconds, 1),
            "maxrss_mb": round(report.maxrss_mb, 1),
            "build_params": {
                "start": start,
                "end": end,
                "exclude_imputed": exclude_imputed,
                "industry_classification_id": industry_classification_id,
            },
            "axes": {
                "n_dates": report.n_dates,
                "n_instruments": report.n_instruments,
                "dates_hash": hash_axis(axes.dates),
                "instruments_hash": hash_axis(axes.instruments),
            },
            "sources": fp_before,
            
            "fields": {
                st.name: {
                    "dtype": st.dtype,
                    "data_hash": report.field_hashes[st.name],
                    "nan_ratio": round(st.nan_ratio, 6),
                    "dropped_rows": st.dropped_rows,
                    "events_dropped_regression": st.events_dropped_regression,
                    "all_nan_instruments": report.all_nan_instruments[st.name],
                    "build_seconds": round(st.build_seconds, 2),
                }
                for st in report.field_stats
            },
        }
        _atomic_write_yaml(tmp_dir / "meta.yaml", meta)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    if final_dir.exists():
        shutil.rmtree(final_dir)
    os.rename(tmp_dir, final_dir)

    return report

# 状态判定与加载

@dataclass
class CacheStatus:

    variant: str
    state: str  
    reasons: list[str] = field(default_factory=list)
    meta: dict | None = None

def read_meta(variant: str) -> dict | None:
    
    path = _variant_dir(variant) / "meta.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def status(variant: str) -> CacheStatus:
    
    meta = read_meta(variant)
    if meta is None:
        return CacheStatus(variant, "missing", ["meta.yaml 不存在（从未构建或构建未完成）"])

    if meta.get("schema_version") != SCHEMA_VERSION:
        return CacheStatus(
            variant,
            "stale",
            [f"schema_version 不匹配：缓存 {meta.get('schema_version')} ≠ 当前 {SCHEMA_VERSION}"],
            meta,
        )

    from providers.markets.cn_a.csmar.manifest import read_manifest

    manifest = read_manifest()
    reasons: list[str] = []
    for table, recorded in (meta.get("sources") or {}).items():
        staged = workspace().staged_for(_MARKET, _INSTRUMENT_CLASS) / f"{table}.parquet"
        if not staged.exists():
            reasons.append(f"{table}：staged 文件已不存在")
            continue
        current = _source_fingerprint(table, manifest)
        diffs = [
            f"{key} {recorded.get(key)} → {current[key]}"
            # A successful empty check advances the download watermark, not the data.
            for key in ("rows", "size_bytes", "mtime_ns")
            if current[key] != recorded.get(key)
        ]
        if diffs:
            reasons.append(f"{table}：{'；'.join(diffs)}")

    if reasons:
        return CacheStatus(variant, "stale", reasons, meta)
    return CacheStatus(variant, "fresh", [], meta)

def load_axes(variant: str) -> tuple[pd.Index, pd.Index]:
    
    base = _variant_dir(variant) / "_axes"
    dates = pd.read_parquet(base / "dates.parquet")["date"]
    instruments = pd.read_parquet(base / "instruments.parquet")["instrument_id"]
    return pd.Index(dates, name="date"), pd.Index(instruments)

def load_field(variant: str, name: str) -> pd.DataFrame:
    
    path = _variant_dir(variant) / "fields" / f"{name}.parquet"
    if not path.exists():
        raise PanelCacheError(
            code="PANEL_FIELD_NOT_CACHED",
            message=f"字段 {name} 未在 {variant} 变体缓存中落盘。",
            context={"variant": variant, "field": name},
            blocking=True,
            suggested_action="执行 data rebuild-cache 重建缓存（确认字段清单包含该字段）。",
        )
    frame = pd.read_parquet(path)
    frame.index.name = "date"
    return frame
