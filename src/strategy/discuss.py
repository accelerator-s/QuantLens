from __future__ import annotations
import yaml
from .store import StrategyProfileStore

_SCHEMA_GUIDE = """\
目标 YAML 结构（StrategyProfileDoc，所有比例用小数字符串如 "0.1"）：
- profile_name: str，仅 [A-Za-z0-9_]
- display_name: str，简洁自然的中文策略名称，面向普通用户展示
- is_template: bool（基础模板=true，禁止激活）
- inheritance_parent: str（父级模板名，可空字符串；子配置整节覆盖父配置）
- market: str（如 CN-A）；instruments: list[str]（如 [stock]）
- timescale / data_frequency / rebalance_frequency:
  daily|weekly|monthly|quarterly|event_driven
- universe: {min_listed_days: int>=0, exclude_suspended: bool, exclude_st: bool, notes: str}
- factors: {weighting: equal|manual, items: [{name, weight(manual 时必填,>0),
  direction_override}]}（因子必须已在因子注册表注册）
- standardization_chain: 步骤列表，step 取值与参数：
  winsorize{lower_pct,upper_pct} / zscore{} / rank{method: average|min|max|dense} /
  industry_neutral{industry_source} / size_neutral{size_field} /
  fillna{method: drop|zero|cross_median|industry_median}
- trading_rules: {market_rule: 市场规则注册表中的规则名}
- execution_assumption: {price_anchor, fill_assumption, notes}
- holding_period: int>0（交易日）
- position_constraint: {top_n: int>0, max_weight_per_name: (0,1], min_cash_ratio: [0,1)}
- fees_slippage: {fee_source: market_rule|manual, slippage_bps>=0,
  manual_fee_bps(仅 manual 时给出)}
- risk_control: {max_single_name_pct: (0,1], max_industry_pct: (0,1], notes}
- price_deviation_threshold: 小数>=0（如 "0.02" 表示 2%）
- extra: dict（实验性扩展位，可为 {}）
"""

_SYSTEM_PROMPT = """\
你是 QuantLens的策略配置讨论助手。用户想通过多轮讨论敲定一份
日频及以上尺度的策略配置（strategy profile），定位是持仓决策辅助，非自动交易。
你的任务：
1. 结合下方只读上下文，通过追问澄清 universe、因子组合、标准化链、调仓与
   风控等设定；每轮聚焦一个问题或一个建议，用中文，简洁专业；
2. 只引用"已注册因子"清单中的因子；用户想用未注册因子时，提醒需先通过
   因子库注册，配置激活时会拒绝未注册因子；
3. 不虚构回测结果或收益数字；配置生成后由统一研究流程完成回测评价；
4. 用户输入 'done' 或 '完成' 时结束讨论，你无需回复。
禁止：要求用户提供 API key、密码等敏感信息。

## 只读上下文
"""

def build_static_context(store: StrategyProfileStore | None = None) -> str:
    
    from factors.registry import FactorRegistry
    from market_rules import MarketRuleRegistry, MarketRuleStatus

    lines: list[str] = ["### 已注册因子"]
    factors = FactorRegistry().list()
    if not factors:
        lines.append("（因子注册表为空。策略引用的因子必须先注册，激活时会校验并拒绝未注册因子。）")
    else:
        for f in factors:
            lines.append(f"- {f.name}（状态 {f.status.value}，方向 {f.direction}）")

    lines.append("\n### ACTIVE 市场规则")
    rules = MarketRuleRegistry().list(status=MarketRuleStatus.ACTIVE)
    if not rules:
        lines.append("（无 ACTIVE 市场规则。trading_rules.market_rule 激活时会校验。）")
    else:
        for r in rules:
            lines.append(f"- {r.name}（market={r.market}, instrument_class={r.instrument_class}）")

    lines.append("\n### 既有策略配置")
    store = store or StrategyProfileStore()
    profiles = store.list()
    if not profiles:
        lines.append("（尚无策略配置。）")
    else:
        for p in profiles:
            kind = "模板" if p.is_template else "策略"
            parent = f"，父级 {p.inheritance_parent}" if p.inheritance_parent else ""
            lines.append(f"- {p.profile_name}（{kind}，状态 {p.status.value}{parent}）")

    lines.append("\n### " + _SCHEMA_GUIDE.splitlines()[0])
    lines.extend(_SCHEMA_GUIDE.splitlines()[1:])
    return "\n".join(lines)
