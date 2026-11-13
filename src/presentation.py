from __future__ import annotations

from exceptions import QuantTrainingError

_FACTOR_NAMES = {
    "analyst_fy1_revision": "分析师盈利预测上调",
    "earnings_announcement_drift": "业绩公告后价格延续",
    "external_lower_shadow_volskew_30d": "下影线与量能偏度交互（已淘汰）",
    "growth_revenue_yoy": "营收增长",
    "industry_momentum_60": "行业景气趋势",
    "institutional_holding_change": "机构增持变化",
    "liquidity_amount_20": "成交活跃度检验（已归档）",
    "low_volatility_20": "低波动检验（已归档）",
    "mom_20": "二十日动量测试（已归档）",
    "quality_cashflow_assets": "经营现金流质量",
    "quality_gross_margin": "毛利率质量",
    "quality_roe_ttm": "净资产收益率检验（已归档）",
    "reversal_5": "短期超跌反弹",
    "risk_adjusted_momentum_12_1": "风险调整动量（已淘汰）",
    "safety_low_leverage": "低杠杆安全性（已淘汰）",
    "value_defensive_quality": "价值质量防御旧版（已取代）",
    "value_defensive_quality_v2": "价值盈利防御",
    "value_earnings_yield": "盈利收益率检验（已归档）",
}

_STRATEGY_NAMES = {
    "a_share_research_base": "A股研究基础规则",
    "candidate_value_quality_defensive": "价值盈利防御候选策略",
    "research_analyst_fy1_revision": "分析师盈利预测研究",
    "research_earnings_announcement_drift": "业绩公告后延续研究",
    "research_external_lower_shadow_volskew_30d": "下影线量能交互因子复现（已淘汰）",
    "research_growth_revenue_yoy": "营收增长研究",
    "research_industry_momentum_60": "行业景气趋势研究",
    "research_institutional_holding_change": "机构增持变化研究",
    "research_liquidity_amount_20": "成交活跃度检验（已归档）",
    "research_low_volatility_20": "低波动检验（已归档）",
    "research_multifactor_core": "早期多因子测试（已归档）",
    "research_quality_cashflow_assets": "经营现金流质量研究",
    "research_quality_gross_margin": "毛利率质量研究",
    "research_quality_roe_ttm": "净资产收益率检验（已归档）",
    "research_reversal_5": "短期超跌反弹研究",
    "research_risk_adjusted_momentum_12_1": "风险调整动量研究（已淘汰）",
    "research_safety_low_leverage": "低杠杆安全性研究（已淘汰）",
    "research_value_defensive_quality": "价值质量防御旧版（已被取代）",
    "research_value_defensive_quality_v2": "价值盈利防御候选策略",
    "research_value_defensive_quality_v2_top50": "价值盈利防御分散持仓测试（已淘汰）",
    "research_value_earnings_yield": "盈利收益率检验（已归档）",
    "smoke_mom20": "二十日动量早期测试（已归档）",
}

ARCHIVED_FACTORS = {
    "external_lower_shadow_volskew_30d",
    "liquidity_amount_20",
    "low_volatility_20",
    "mom_20",
    "quality_roe_ttm",
    "risk_adjusted_momentum_12_1",
    "safety_low_leverage",
    "value_defensive_quality",
    "value_earnings_yield",
}

ARCHIVED_STRATEGIES = {
    "research_external_lower_shadow_volskew_30d",
    "research_liquidity_amount_20",
    "research_low_volatility_20",
    "research_multifactor_core",
    "research_quality_roe_ttm",
    "research_risk_adjusted_momentum_12_1",
    "research_safety_low_leverage",
    "research_value_defensive_quality",
    "research_value_defensive_quality_v2_top50",
    "research_value_earnings_yield",
    "smoke_mom20",
}

def factor_display_name(name: str) -> str:
    
    fixed = _FACTOR_NAMES.get(name)
    if fixed:
        return fixed
    try:
        from factors.registry import FactorRegistry

        return FactorRegistry().get(name).display_name or name
    except QuantTrainingError:
        from quantlens.research_candidate import research_base_name
        base = research_base_name(name)
        if base:
            try:
                return FactorRegistry().get(base).display_name or base
            except QuantTrainingError:
                pass
        return name

def strategy_display_name(name: str | None) -> str:
    
    if not name:
        return ""
    fixed = _STRATEGY_NAMES.get(name)
    if fixed:
        return fixed
    try:
        from strategy.store import StrategyProfileStore

        return StrategyProfileStore().get(name).display_name or name
    except QuantTrainingError:
        return name

def experiment_display_name(subject_type: str, subject_name: str) -> str:
    
    if subject_type == "factor":
        return f"{factor_display_name(subject_name)}验证"
    if subject_type == "strategy":
        return f"{strategy_display_name(subject_name)}历史验证"
    if subject_type == "portfolio_policy":
        return f"{strategy_display_name(subject_name)}组合规则验证"
    return "研究实验"

def experiment_is_archived(subject_type: str, subject_name: str, profile_name: str | None) -> bool:
    
    if subject_type == "factor" and subject_name in ARCHIVED_FACTORS:
        return True
    strategy_name = profile_name or (subject_name if subject_type != "factor" else "")
    return strategy_name in ARCHIVED_STRATEGIES

__all__ = [
    "ARCHIVED_FACTORS",
    "ARCHIVED_STRATEGIES",
    "experiment_display_name",
    "experiment_is_archived",
    "factor_display_name",
    "strategy_display_name",
]
