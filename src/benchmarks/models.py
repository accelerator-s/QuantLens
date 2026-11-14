from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class IndexWeight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1)


class ReturnBenchmark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["same_cluster_equal_weight", "universe_equal_weight", "universe_float_cap_weight",
                  "industry_equal_weight", "index", "blend", "cash"]
    code: str | None = None
    components: list[IndexWeight] = Field(default_factory=list)
    cash_weight: float = Field(default=0, ge=0, le=1)
    rebalance: Literal["daily", "monthly"] = "monthly"

    @model_validator(mode="after")
    def shape(self):
        if (self.kind == "index") != bool(self.code):
            raise ValueError("只有指数基准需要 code。")
        if self.kind == "blend":
            if not self.components or abs(sum(c.weight for c in self.components) + self.cash_weight - 1) > 1e-8:
                raise ValueError("混合基准的指数及现金权重必须合计为 1。")
            if len({c.code for c in self.components}) != len(self.components):
                raise ValueError("混合基准不能重复引用指数。")
        elif self.components or self.cash_weight:
            raise ValueError("只有混合基准可以设置组成及现金权重。")
        return self


class BenchmarkPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_benchmark: ReturnBenchmark
    market_reference: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class MarketReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1)
    source: Literal['csmar'] = 'csmar'
    selection_timing: Literal['before_backtest', 'after_backtest_display']
    selection_model: str = ''
    rationale: str = Field(min_length=1)
    selected_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    catalogue_hash: str = ''
    selection_window: dict[str, str] = Field(default_factory=dict)


class ReferencePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    nav: float | None


class ReferenceComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    source: Literal['csmar'] = 'csmar'
    return_type: Literal['price', 'total', 'net_total']
    selection_timing: Literal['before_backtest', 'after_backtest_display']
    status: Literal['available', 'partial', 'unavailable']
    total_return: float | None = None
    excess_return: float | None = None
    message: str = ''
    data_hash: str | None = None
    missing_dates: list[str] = Field(default_factory=list)
    items: list[ReferencePoint] = Field(default_factory=list)


BENCHMARK_NAMES = {
    "same_cluster_equal_weight": "同风格簇等权", "universe_equal_weight": "股票池等权",
    "universe_float_cap_weight": "股票池流通市值加权", "industry_equal_weight": "同行业等权",
    "index": "全收益指数", "cash": "现金零收益", "blend": "自定义混合基准",
}
