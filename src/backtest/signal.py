from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from exceptions import BacktestError
from strategy import ResolvedStrategyProfile


@dataclass
class SignalResult:

    scores: pd.DataFrame

    factor_code_hashes: dict[str, str]

    directions: dict[str, int]

    notes: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

@dataclass(frozen=True)
class RuntimeFactor:

    source: str
    dependencies: tuple[str, ...]
    direction: str
    standardization: tuple[str,...] | None = None
    missing_handling: str = ''
    parameters: dict = field(default_factory=dict)

# 因子执行

def _resolve_direction(name: str, override: str, meta_direction: str) -> int:
    declared = override.strip() or meta_direction
    if declared == "positive":
        return 1
    if declared == "negative":
        return -1
    raise BacktestError(
        code="BACKTEST_FACTOR_DIRECTION_UNDECIDED",
        message=(
            f"因子 {name} 方向为 {declared or 'undecided'}，回测无法确定多空方向。"
            "禁止用样本内 IC 推断（前瞻信息）。"
        ),
        blocking=True,
        suggested_action=(
            "先用 `factor evaluate` 在样本外确认方向，然后在策略配置的因子清单里"
            "设置 direction_override（positive/negative），或更新注册表 direction。"
        ),
        related_subjects={"factor": name},
    )

def compute_composite_scores(
    profile: ResolvedStrategyProfile,
    *,
    start: str,
    end: str,
    lookback_buffer_days: int,
    runtime_factors: dict[str, RuntimeFactor] | None = None,
) -> SignalResult:
    
    if runtime_factors:
        from quantlens.research_candidate import prepare_profile
        profile=prepare_profile(profile,runtime_factors)
    import os
    if not os.path.isdir('/workspace') and os.environ.get('QUANTLENS_COMPUTE_LOCAL')!='1':
        from quantlens.research_data import extract
        from factors.registry import FactorRegistry
        frame=extract({'kind':'signal','profile':profile.model_dump(mode='json'),'start':start,'end':end})
        scores=frame.pivot(index='date',columns='code',values='score').sort_index()
        metas={ref.name:FactorRegistry().get(ref.name) for ref in profile.factors.items}
        evidence=frame.attrs.get('lineage',{})
        hashes={name:','.join(item['code_hashes']) for name,item in evidence.get('upstream',{}).items()}
        return SignalResult(scores=scores,factor_code_hashes=hashes,directions={ref.name:_resolve_direction(ref.name,ref.direction_override,metas[ref.name].direction) for ref in profile.factors.items},notes=['读取已发布 DWS 合成信号'],provenance=evidence)
    from quantlens.backtest_inputs import inputs
    return inputs(profile,start,end)["signal"]

__all__ = ['SignalResult','RuntimeFactor','compute_composite_scores']
