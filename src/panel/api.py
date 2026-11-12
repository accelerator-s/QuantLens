from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from exceptions import PanelCacheError

from .cache import CacheStatus, load_field, status
from .contract import (
    FieldCategory,
    FieldSpec,
    panel_field_names,
    resolve_fields,
)
from .universe import _ST_STATUS_CODES, UniverseConfig, build_universe_mask

__all__ = ["cache_status", "load_panel", "panel_field_names"]

def cache_status() -> dict[str, CacheStatus]:
    
    return {variant: status(variant) for variant in ("default", "strict")}

def _require_fresh(variant: str) -> dict:
    
    st = status(variant)
    if st.state == "missing":
        raise PanelCacheError(
            code="PANEL_CACHE_MISSING",
            message=f"矩阵缓存（{variant} 变体）不存在。",
            context={"variant": variant, "reasons": st.reasons},
            blocking=True,
            suggested_action="先执行 data rebuild-cache 构建缓存。",
        )
    if st.state == "stale":
        raise PanelCacheError(
            code="PANEL_CACHE_STALE",
            message=f"矩阵缓存（{variant} 变体）已失效：源数据在缓存构建后发生变更。",
            context={"variant": variant, "reasons": st.reasons},
            blocking=True,
            suggested_action="执行 data rebuild-cache 重建缓存后重试（不会静默现场重算）。",
        )
    assert st.meta is not None
    return st.meta

def _variant_for(spec: FieldSpec, exclude_imputed: bool) -> str:
    
    if exclude_imputed and spec.category is FieldCategory.FUNDAMENTAL:
        return "strict"
    return "default"

def _slice_rows(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start is None and end is None:
        return frame
    
    return frame.loc[slice(start, end)]

def _check_range(meta: dict, start: str | None, end: str | None) -> None:
    params = meta.get("build_params") or {}
    cached_start, cached_end = params.get("start"), params.get("end")
    problems = []
    if start is not None and cached_start and start < cached_start:
        problems.append(f"请求起点 {start} 早于缓存起点 {cached_start}")
    if end is not None and cached_end and end > cached_end:
        problems.append(f"请求终点 {end} 晚于缓存终点 {cached_end}")
    if problems:
        raise PanelCacheError(
            code="PANEL_RANGE_UNCOVERED",
            message=f"请求区间超出缓存覆盖范围：{'；'.join(problems)}。",
            context={"cached_start": cached_start, "cached_end": cached_end},
            blocking=True,
            suggested_action="缩小请求区间，或用 data rebuild-cache --start/--end 扩建缓存。",
        )

def load_panel(
    fields: Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    universe: UniverseConfig | None = None,
    exclude_imputed: bool = False,
    industry_classification_id: str | None = None,
    as_datetime_index: bool = True,
) -> dict[str, pd.DataFrame]:
    
    if start is not None and end is not None and start > end:
        raise PanelCacheError(
            code="PANEL_INVALID_RANGE",
            message=f"请求区间起点晚于终点：start={start} > end={end}。",
            context={"start": start, "end": end},
            blocking=True,
            suggested_action="检查日期参数顺序（须 start ≤ end）。",
        )

    specs = resolve_fields(list(fields) if fields is not None else None)

    needed_variants = {_variant_for(s, exclude_imputed) for s in specs}
    if universe is not None:
        needed_variants.add("default")  

    metas = {v: _require_fresh(v) for v in sorted(needed_variants)}

    if len(metas) > 1:
        
        ax_d, ax_s = metas["default"]["axes"], metas["strict"]["axes"]
        if (ax_d["dates_hash"], ax_d["instruments_hash"]) != (
            ax_s["dates_hash"],
            ax_s["instruments_hash"],
        ):
            raise PanelCacheError(
                code="PANEL_VARIANT_MISMATCH",
                message="default 与 strict 变体的面板轴不一致，无法混合加载。",
                context={"default_axes": ax_d, "strict_axes": ax_s},
                blocking=True,
                suggested_action="用相同 --start/--end 依次重建两个变体后重试。",
            )

    for meta in metas.values():
        _check_range(meta, start, end)

    if industry_classification_id is not None and any(
        s.category is FieldCategory.INDUSTRY for s in specs
    ):
        built_with = (metas["default"].get("build_params") or {}).get("industry_classification_id")
        if built_with != industry_classification_id:
            raise PanelCacheError(
                code="PANEL_INDUSTRY_MISMATCH",
                message=(
                    f"请求行业分类体系 {industry_classification_id}，但缓存按 {built_with} 构建。"
                ),
                context={"requested": industry_classification_id, "cached": built_with},
                blocking=True,
                suggested_action=(
                    "用 data rebuild-cache --industry 重建缓存，或改用缓存现有体系。"
                ),
            )

    mask: pd.DataFrame | None = None
    if universe is not None:
        is_trading = _slice_rows(load_field("default", "is_trading"), start, end)
        listed_days = _slice_rows(load_field("default", "listed_days"), start, end)
        trading_status = None
        if universe.exclude_st and _ST_STATUS_CODES is not None:
            trading_status = _slice_rows(load_field("default", "trading_status"), start, end)
        mask = build_universe_mask(
            universe,
            is_trading=is_trading,
            listed_days=listed_days,
            trading_status=trading_status,
        )

    panel: dict[str, pd.DataFrame] = {}
    for spec in specs:
        frame = _slice_rows(load_field(_variant_for(spec, exclude_imputed), spec.name), start, end)
        if mask is not None and spec.category is not FieldCategory.DERIVED:
            frame = frame.where(mask)
        if as_datetime_index:
            frame = frame.set_axis(pd.to_datetime(frame.index), axis=0)
        panel[spec.name] = frame

    return panel
