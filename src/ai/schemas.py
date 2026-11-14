from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from exceptions import ContractViolation

SCHEMA_VERSION = 1

class _BaseAIOutput(BaseModel):

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    task: str

    produced_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    model_name: str


    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""

class FactorCandidate(_BaseAIOutput):
    task: Literal["factor_candidate"] = "factor_candidate"
    factor_name: str
    display_name: str = ""

    assumption: str
    direction: Literal["positive", "negative"]
    timescale: Literal["daily", "weekly", "monthly", "quarterly", "event_driven"]
    dependencies: list[str] = Field(default_factory=list)

    parameters: dict[str, Any] = Field(default_factory=dict)
    standardization: list[str] = Field(default_factory=list)
    missing_handling: Literal["drop", "fill"] = "drop"
    code: str

    evaluation_metric_hints: list[str] = Field(default_factory=list)

    @field_validator("factor_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").isalnum():
            raise ValueError("factor_name 只允许 [A-Za-z0-9_]。")
        return v

    @field_validator("dependencies")
    @classmethod
    def _deps_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))

class ProviderFieldMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_field: str
    target_field: str
    target_table: str
    transform: str = ""
    notes: str = ""

class ProviderMapping(_BaseAIOutput):
    task: Literal["provider_mapping"] = "provider_mapping"
    provider: str
    target_tables: list[str] = Field(default_factory=list)
    mappings: list[ProviderFieldMapping] = Field(min_length=1)
    coverage_notes: str = ""
    risks: list[str] = Field(default_factory=list)

class StrategyCritique(_BaseAIOutput):
    task: Literal["strategy_critique"] = "strategy_critique"
    profile_name: str
    findings: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)

class MemoryCandidate(_BaseAIOutput):
    task: Literal["memory_candidate"] = "memory_candidate"
    category: Literal[
        "trading_preference",
        "timescale_preference",
        "risk_preference",
        "rebalance_aversion",
        "provider_decision",
        "strategy_choice",
        "review_summary",
        "execution_deviation",
        "factor_retired",
        "framework_lesson",
    ]
    summary: str
    detail: str = ""
    tags: list[str] = Field(default_factory=list)
    related_files: list[str] = Field(default_factory=list)
    contains_secret: Literal[False] = False

    FORBIDDEN_PATTERNS: ClassVar[tuple[str, ...]] = (
        "api_key",
        "apikey",
        "token",
        "password",
        "passwd",
        "secret",
        "cookie",
        "authorization",
    )

    @field_validator("summary", "detail")
    @classmethod
    def _no_secret_keywords(cls, v: str) -> str:
        low = v.lower()
        for pat in cls.FORBIDDEN_PATTERNS:
            if pat in low:
                raise ValueError(f"memory 候选可能含敏感字段（命中：{pat}），拒绝入库。")
        return v

class StructuredConfig(_BaseAIOutput):
    task: Literal["structured_config"] = "structured_config"
    config_kind: str

    proposed_value: dict[str, Any]
    open_questions: list[str] = Field(default_factory=list)

class DataQualityMerge(_BaseAIOutput):
    task: Literal["data_quality_merge"] = "data_quality_merge"
    standard_field: str
    sources: list[str] = Field(min_length=2)
    pattern: Literal[
        "numeric_diff",
        "categorical_diff",
        "missing_in_source",
        "temporal_lag",
        "unit_mismatch",
    ]
    proposed_merge_rule: str
    impact_window: str
    backfill_plan: str = ""

class RiskReview(_BaseAIOutput):
    task: Literal["risk_review"] = "risk_review"
    plan_id: str
    blocking_findings: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)

class CheapBatchLabel(_BaseAIOutput):
    task: Literal["cheap_batch_label"] = "cheap_batch_label"
    items: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def _shape(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for it in v:
            if "id" not in it or "label" not in it:
                raise ValueError("cheap_batch_label.items 每项必须含 id 与 label。")
        return v

from benchmarks.models import ReturnBenchmark


class BenchmarkSelection(_BaseAIOutput):
    task: Literal["benchmark_selection"] = "benchmark_selection"
    return_benchmark: ReturnBenchmark
    market_reference: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class MarketReferenceSelection(_BaseAIOutput):
    task: Literal['market_reference_selection'] = 'market_reference_selection'
    code: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


SCHEMA_REGISTRY: dict[str, type[_BaseAIOutput]] = {
    "market_reference_selection": MarketReferenceSelection,
    "benchmark_selection": BenchmarkSelection,
    "factor_candidate": FactorCandidate,
    "provider_mapping": ProviderMapping,
    "strategy_critique": StrategyCritique,
    "memory_candidate": MemoryCandidate,
    "structured_config": StructuredConfig,
    "data_quality_merge": DataQualityMerge,
    "risk_review": RiskReview,
    "cheap_batch_label": CheapBatchLabel,
}

def validate_ai_output(task: str, payload: dict[str, Any]) -> _BaseAIOutput:
    
    cls = SCHEMA_REGISTRY.get(task)
    if cls is None:
        raise ContractViolation(
            code="AI_OUTPUT_TASK_UNKNOWN",
            message=f"未注册的 AI 任务输出 schema：{task}",
            context={"task": task, "available": sorted(SCHEMA_REGISTRY)},
            blocking=True,
            suggested_action="先在 ai/schemas.py 中加 schema，并在能力矩阵中声明任务。",
        )
    try:
        return cls.model_validate(payload)
    except Exception as exc:  
        raise ContractViolation(
            code="AI_OUTPUT_SCHEMA_INVALID",
            message=f"AI 输出未通过 schema 校验：{exc}",
            context={"task": task},
            blocking=True,
            suggested_action="检查模型是否真的输出 JSON，是否字段缺失或类型不符。",
        ) from exc

__all__ = [
    "SCHEMA_REGISTRY",
    "SCHEMA_VERSION",
    "CheapBatchLabel",
    "DataQualityMerge",
    "FactorCandidate",
    "MemoryCandidate",
    "ProviderFieldMapping",
    "ProviderMapping",
    "RiskReview",
    "StrategyCritique",
    "StructuredConfig",
    "validate_ai_output",
]
