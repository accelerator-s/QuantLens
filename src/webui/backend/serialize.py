from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

def json_safe(obj: Any) -> Any:
    
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (datetime, date, pd.Timestamp)):
        if isinstance(obj, pd.Timestamp) and pd.isna(obj):
            return None
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, np.generic):
        return json_safe(obj.item())
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return df_records(obj)
    if isinstance(obj, pd.Series):
        return [json_safe(v) for v in obj.tolist()]
    if hasattr(obj, "model_dump"):
        
        return json_safe(obj.model_dump(mode="json"))
    if hasattr(obj, "__dataclass_fields__"):
        return {k: json_safe(getattr(obj, k)) for k in obj.__dataclass_fields__}
    return str(obj)

def df_records(
    frame: pd.DataFrame,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    
    if frame.empty:
        return []
    df = frame
    if df.index.name is not None or not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    if offset:
        df = df.iloc[offset:]
    if limit is not None:
        df = df.iloc[:limit]
    df = df.astype(object).where(pd.notna(df), None)
    return [{str(k): json_safe(v) for k, v in row.items()} for row in df.to_dict("records")]

def downsample_indices(n: int, target: int, *, keep: list[int] | None = None) -> list[int]:
    
    if n <= target:
        return list(range(n))
    step = (n - 1) / (target - 1)
    picked = {round(i * step) for i in range(target)}
    picked.add(0)
    picked.add(n - 1)
    for idx in keep or []:
        if 0 <= idx < n:
            picked.add(idx)
    return sorted(picked)
