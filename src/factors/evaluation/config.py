from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

class Neutralization(StrEnum):

    RAW = "raw"

    INDUSTRY = "industry"

    SIZE = "size"

    INDUSTRY_SIZE = "industry_size"

class EvaluationConfig(BaseModel):

    model_config = ConfigDict(extra="forbid")

    factor_name: str
    start: str
    
    end: str

    return_field: Literal["ret_wd", "ret_nd", "close_raw"] = "ret_wd"

    primary_horizon: int = 1

    decay_horizons: list[int] = Field(default_factory=lambda: [1, 2, 3, 5, 10, 20])

    group_counts: list[int] = Field(default_factory=lambda: [5, 10])

    neutralizations: list[Neutralization] = Field(default_factory=lambda: [Neutralization.INDUSTRY])

    industry_classification_id: str = "P0221"

    standardize: Literal["zscore", "none"] = "zscore"

    min_listed_days: int = 120

    exclude_suspended_entry: bool = True

    exclude_limit_locked_entry: bool = True

    min_cross_section: int = 30

    strict_compare: bool = False

    lookback_buffer_days: int = 250

    forward_buffer_days: int = 90

    trading_days_per_year: int = 244

    @field_validator("start", "end")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            raise ValueError(f"日期须为 YYYY-MM-DD 格式：{v}")
        return v

    @field_validator("decay_horizons", "group_counts")
    @classmethod
    def _positive_sorted(cls, v: list[int]) -> list[int]:
        if not v or any(x <= 0 for x in v):
            raise ValueError("列表须非空且全为正整数。")
        return sorted(set(v))

    @field_validator("primary_horizon")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("primary_horizon 须为正整数。")
        return v

    def variants(self) -> list[Neutralization]:
        
        out = [Neutralization.RAW]
        for n in self.neutralizations:
            if n is not Neutralization.RAW and n not in out:
                out.append(n)
        return out

    def max_horizon(self) -> int:
        return max([*self.decay_horizons, self.primary_horizon])

    def needs_industry(self) -> bool:
        return any(
            n in (Neutralization.INDUSTRY, Neutralization.INDUSTRY_SIZE) for n in self.variants()
        )

    def needs_size(self) -> bool:
        return any(
            n in (Neutralization.SIZE, Neutralization.INDUSTRY_SIZE) for n in self.variants()
        )

    def content_hash(self) -> str:
        
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

__all__ = ["EvaluationConfig", "Neutralization"]
