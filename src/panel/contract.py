from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from exceptions import PanelCacheError

class FieldCategory(StrEnum):

    PRICE = "price"  
    ADJUSTED = "adjusted"  
    FUNDAMENTAL = "fundamental"  
    INDUSTRY = "industry"  
    AGGREGATE = "aggregate"  
    DERIVED = "derived"  

@dataclass(frozen=True)
class FieldSpec:

    name: str
    category: FieldCategory
    source_table: str  
    source_column: str  
    dtype: str  
    description: str

def _price(name: str, desc: str) -> FieldSpec:
    return FieldSpec(name, FieldCategory.PRICE, "prices_daily", name, "float64", desc)

def _adjusted(name: str, desc: str, dtype: str = "float64") -> FieldSpec:
    return FieldSpec(name, FieldCategory.ADJUSTED, "returns_daily", name, dtype, desc)

def _fund(table: str, name: str, desc: str) -> FieldSpec:
    return FieldSpec(name, FieldCategory.FUNDAMENTAL, table, name, "float64", desc)

def _agg(table: str, name: str, desc: str) -> FieldSpec:
    return FieldSpec(name, FieldCategory.AGGREGATE, table, name, "float64", desc)

FIELD_REGISTRY: tuple[FieldSpec, ...] = (
    
    _price("open", "开盘价（未复权）"),
    _price("high", "最高价（未复权）"),
    _price("low", "最低价（未复权）"),
    _price("close", "收盘价（未复权）"),
    _price("volume", "成交量（股）"),
    _price("amount", "成交金额（元）"),
    
    _adjusted("ret_wd", "日个股回报率（考虑现金红利再投资）"),
    _adjusted("ret_nd", "日个股回报率（不考虑现金红利）"),
    _adjusted("adj_close_wd", "可比收盘价（考虑现金红利再投资）"),
    _adjusted("adj_close_nd", "可比收盘价（不考虑现金红利）"),
    _adjusted("mktcap_float", "日个股流通市值（千元）"),
    _adjusted("mktcap_total", "日个股总市值（千元）"),
    _adjusted("trading_status", "交易状态（CSMAR Trdsta 原始编码）", "string"),
    
    _fund("fs_balance", "cash", "货币资金"),
    _fund("fs_balance", "accounts_receivable", "应收账款"),
    _fund("fs_balance", "inventory", "存货"),
    _fund("fs_balance", "current_assets", "流动资产合计"),
    _fund("fs_balance", "fixed_assets", "固定资产"),
    _fund("fs_balance", "total_assets", "资产总计"),
    _fund("fs_balance", "short_term_debt", "短期借款"),
    _fund("fs_balance", "current_liabilities", "流动负债合计"),
    _fund("fs_balance", "long_term_debt", "长期借款"),
    _fund("fs_balance", "total_liabilities", "负债合计"),
    _fund("fs_balance", "equity_parent", "归属母公司股东权益"),
    _fund("fs_balance", "total_equity", "股东权益合计"),
    
    _fund("fs_income", "total_revenue", "营业总收入"),
    _fund("fs_income", "revenue", "营业收入"),
    _fund("fs_income", "total_operating_cost", "营业总成本"),
    _fund("fs_income", "operating_cost", "营业成本"),
    _fund("fs_income", "operating_profit", "营业利润"),
    _fund("fs_income", "profit_before_tax", "利润总额"),
    _fund("fs_income", "net_profit", "净利润"),
    _fund("fs_income", "net_profit_parent", "归属母公司净利润"),
    
    _fund("fs_cashflow", "cfo_net", "经营活动现金流量净额"),
    _fund("fs_cashflow", "cfi_net", "投资活动现金流量净额"),
    _fund("fs_cashflow", "capex_cash", "购建固定资产等支付的现金"),
    _fund("fs_cashflow", "cff_net", "筹资活动现金流量净额"),
    _fund("fs_cashflow", "cash_net_increase", "现金及现金等价物净增加额"),
    
    _fund("fundamental_ttm", "net_profit_ttm_parent", "归属母公司净利润（TTM）"),
    _fund("fundamental_ttm", "roe_ttm", "ROE（TTM 归母净利润 / 当期归母权益，权益≤0 记缺失）"),
    _fund("fundamental_ttm", "gross_margin_ttm", "毛利率（TTM，(营业收入-营业成本)/营业收入）"),
    
    _agg(
        "consensus_daily",
        "consensus_fy1_mean",
        "FY1 一致预期净利润均值（近180日各机构最新预测；元）",
    ),
    _agg("consensus_daily", "consensus_fy1_median", "FY1 一致预期净利润中位数（元）"),
    _agg("consensus_daily", "consensus_fy1_coverage", "FY1 覆盖机构数（0 = 窗口内无有效预测）"),
    _agg("consensus_daily", "consensus_fy1_year", "当前 FY1 会计年度（每年 4-30/5-1 统一切换）"),
    
    _agg(
        "holder_longline",
        "inst_longline_ratio",
        "十大流通股东中长线机构合计持股比例（%，占总股本，披露日生效）",
    ),
    _agg(
        "holder_longline",
        "inst_longline_chg",
        "长线机构合计持股比例季度环比变化（%点；仅相邻季度）",
    ),
    
    FieldSpec(
        "industry_code",
        FieldCategory.INDUSTRY,
        "industry_classification",
        "industry_code",
        "string",
        "行业代码（分类体系由构建参数 industry_classification_id 决定）",
    ),
    
    FieldSpec(
        "is_trading",
        FieldCategory.DERIVED,
        "derived",
        "close.notna()",
        "bool",
        "当日是否有成交（False = 停牌 / 未上市 / 已退市）",
    ),
    FieldSpec(
        "listed_days",
        FieldCategory.DERIVED,
        "derived",
        "全历史交易日下标 - 首个交易日下标 + 1（含停牌日，上市前为 0）",
        "int32",
        "距首个交易日的日历级交易日数（次新股过滤用）",
    ),
)

_FIELDS_BY_NAME: dict[str, FieldSpec] = {spec.name: spec for spec in FIELD_REGISTRY}

assert len(_FIELDS_BY_NAME) == len(FIELD_REGISTRY), "FIELD_REGISTRY 存在重复字段名"

def get_field(name: str) -> FieldSpec:
    
    spec = _FIELDS_BY_NAME.get(name)
    if spec is None:
        raise PanelCacheError(
            code="PANEL_UNKNOWN_FIELD",
            message=f"未知面板字段：{name}。",
            context={"known_fields": sorted(_FIELDS_BY_NAME)},
            blocking=True,
            suggested_action="在因子库查看可用字段。",
        )
    return spec

def resolve_fields(names: Sequence[str] | None = None) -> list[FieldSpec]:
    
    if names is None:
        return list(FIELD_REGISTRY)
    return [get_field(n) for n in names]

def list_fields(category: FieldCategory | None = None) -> list[FieldSpec]:
    
    if category is None:
        return list(FIELD_REGISTRY)
    return [spec for spec in FIELD_REGISTRY if spec.category is category]

def panel_field_names() -> list[str]:
    
    return [spec.name for spec in FIELD_REGISTRY]
