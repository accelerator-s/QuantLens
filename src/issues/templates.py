from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import IssueType

FieldKind = Literal["text", "long_text", "choice", "multi_choice", "bool"]

@dataclass
class FieldChoice:

    value: str
    label: str
    description: str = ""

@dataclass
class TemplateField:

    key: str
    label: str
    kind: FieldKind = "text"
    choices: list[FieldChoice] = field(default_factory=list)
    required: bool = True
    help: str = ""
    section: str = ""

@dataclass
class IssueTemplate:
    intro: str
    fields: list[TemplateField] = field(default_factory=list)

_FREQ_PURPOSE = [
    FieldChoice("history_authoritative", "history_authoritative", "历史权威"),
    FieldChoice("daily_increment", "daily_increment", "日频增量"),
    FieldChoice("near_realtime", "near_realtime", "近实时监控"),
    FieldChoice("financial_event", "financial_event", "财报事件"),
    FieldChoice("trading_rule", "trading_rule", "交易规则"),
    FieldChoice("human_holding", "human_holding", "人工持仓"),
    FieldChoice("metadata", "metadata", "元数据"),
]

_AUTH_METHODS = [
    FieldChoice("none", "none", "无需鉴权"),
    FieldChoice("api_key", "api_key", "API Key / Token"),
    FieldChoice("oauth", "oauth", "OAuth"),
    FieldChoice("cookie_session", "cookie_session", "Cookie 会话（注意不写入 issue）"),
    FieldChoice("username_password", "username_password", "用户名密码（注意不写入 issue）"),
    FieldChoice("other", "other", "其他（在备注中说明）"),
]

_TIMESCALES = [
    FieldChoice("daily", "daily", "日频"),
    FieldChoice("weekly", "weekly", "周频"),
    FieldChoice("monthly", "monthly", "月频"),
    FieldChoice("quarterly", "quarterly", "季频"),
    FieldChoice("event_driven", "event_driven", "事件驱动"),
]

_DIRECTIONS = [
    FieldChoice("long_only_positive", "long_only_positive", "做多 / 因子值越大越好"),
    FieldChoice("long_only_negative", "long_only_negative", "做多 / 因子值越小越好"),
    FieldChoice("long_short", "long_short", "多空"),
    FieldChoice("undecided", "undecided", "待评估"),
]

_STANDARDIZATION = [
    FieldChoice("zscore", "zscore", "z-score 标准化"),
    FieldChoice("rank", "rank", "rank 排序"),
    FieldChoice("winsorize", "winsorize", "winsorize 缩尾"),
    FieldChoice("industry_neutral", "industry_neutral", "行业中性"),
    FieldChoice("no_standardization", "no_standardization", "不做标准化"),
]

_MISSING = [
    FieldChoice("drop", "drop", "丢弃"),
    FieldChoice("fill_zero", "fill_zero", "填 0"),
    FieldChoice("forward_fill", "forward_fill", "前向填充"),
    FieldChoice("model_specific", "model_specific", "因子内部特殊处理"),
]

_PRICE_ANCHORS = [
    FieldChoice("open", "open", "开盘"),
    FieldChoice("close", "close", "收盘"),
    FieldChoice("vwap", "vwap", "VWAP"),
    FieldChoice("twap", "twap", "TWAP"),
    FieldChoice("custom", "custom", "自定义（在备注中说明）"),
]

_T_PLUS = [
    FieldChoice("T+0", "T+0", "当日可卖或当日结算"),
    FieldChoice("T+1", "T+1", "次一交易日可卖或结算"),
    FieldChoice("T+2", "T+2", "第二个交易日可卖或结算"),
    FieldChoice("T+3", "T+3", "第三个交易日可卖或结算"),
    FieldChoice("other", "other", "其他（在备注中说明）"),
]

_FACTOR_SUBACTION = [
    FieldChoice("new", "new", "新增"),
    FieldChoice("modify", "modify", "修改"),
    FieldChoice("delete", "delete", "删除"),
    FieldChoice("disable", "disable", "禁用"),
    FieldChoice("evaluate", "evaluate", "评估"),
    FieldChoice("restore", "restore", "复活"),
    FieldChoice("retire", "retire", "淘汰"),
    FieldChoice("weight_suggestion", "weight_suggestion", "组合权重建议"),
]

_PORTFOLIO_SUBACTION = [
    FieldChoice("snapshot", "snapshot", "提交快照"),
    FieldChoice("trades", "trades", "录入成交"),
    FieldChoice("plan", "plan", "调仓计划"),
    FieldChoice("reconcile", "reconcile", "核对旧计划"),
    FieldChoice("reinit", "reinit", "账户重新初始化"),
    FieldChoice("unscheduled_trade", "unscheduled_trade", "计划外交易"),
    FieldChoice("price_deviation", "price_deviation", "价格偏差超阈值"),
]

_MEMORY_SUBACTION = [
    FieldChoice("candidate_review", "candidate_review", "复盘候选评审"),
    FieldChoice("commit", "commit", "确认入库"),
    FieldChoice("cleanup", "cleanup", "清理"),
    FieldChoice("external_upgrade", "external_upgrade", "外部记忆框架升级提醒"),
    FieldChoice("migration", "migration", "迁移脚本"),
    FieldChoice("degrade_mode", "degrade_mode", "降级模式问题"),
]

_CLI_FAMILIES = [
    FieldChoice("issue", "issue", "本地 issue 管理"),
    FieldChoice("configure", "configure", "模型、策略和市场规则配置"),
    FieldChoice("provider", "provider", "数据源接入与验收"),
    FieldChoice("data", "data", "数据更新、校验和缓存"),
    FieldChoice("factor", "factor", "因子注册、评估和治理"),
    FieldChoice("backtest", "backtest", "回测与实验"),
    FieldChoice("portfolio", "portfolio", "持仓、成交和调仓计划"),
    FieldChoice("recommend", "recommend", "选股和调仓建议"),
    FieldChoice("recover", "recover", "长时间未登录后的恢复流程"),
    FieldChoice("memory", "memory", "复盘和记忆入库"),
    FieldChoice("doctor", "doctor", "工作区体检"),
    FieldChoice("new", "new", "新增命令"),
]

_FRAMEWORK_MODULES = [
    FieldChoice("data_contracts", "data_contracts", "数据契约"),
    FieldChoice("providers", "providers", "数据源适配"),
    FieldChoice("etl", "etl", "数据抽取、转换和加载"),
    FieldChoice("cache", "cache", "矩阵缓存"),
    FieldChoice("backtest", "backtest", "回测框架"),
    FieldChoice("factors", "factors", "因子系统"),
    FieldChoice("experience", "experience", "经验库"),
    FieldChoice("portfolio", "portfolio", "持仓与调仓"),
    FieldChoice("market_rules", "market_rules", "市场规则"),
    FieldChoice("ai", "ai", "模型路由与权限"),
    FieldChoice("memory", "memory", "复盘与记忆"),
    FieldChoice("issues", "issues", "本地 issue 系统"),
    FieldChoice("cli", "cli", "控制台交互"),
    FieldChoice("paths", "paths", "工作区路径"),
    FieldChoice("exceptions", "exceptions", "结构化异常"),
    FieldChoice("cross_module", "cross_module", "跨多个模块"),
]

_BUG_SCOPE = [
    FieldChoice("data", "data", "数据流程"),
    FieldChoice("cli", "cli", "控制台交互"),
    FieldChoice("backtest", "backtest", "回测流程"),
    FieldChoice("portfolio", "portfolio", "持仓与成交"),
    FieldChoice("memory", "memory", "复盘与记忆"),
    FieldChoice("issue_system", "issue_system", "本地 issue 系统"),
    FieldChoice("ai_routing", "ai_routing", "模型路由"),
    FieldChoice("multiple", "multiple", "多模块"),
]

_DUP_POLICY = [
    FieldChoice("merge_rule_required", "merge_rule_required", "需先敲定合并规则（推荐）"),
    FieldChoice("source_priority", "source_priority", "按来源优先级"),
    FieldChoice("latest_wins", "latest_wins", "最近一份覆盖（仅限低风险字段）"),
]

_CONFLICT_HANDLING = [
    FieldChoice("emit_quality_event", "emit_quality_event", "记录 data_quality_event"),
    FieldChoice(
        "open_data_quality_conflict_issue",
        "open_data_quality_conflict_issue",
        "立刻开 data_quality_conflict issue",
    ),
]

_CONFLICT_PATTERN = [
    FieldChoice("numeric_diff", "numeric_diff", "数值差异"),
    FieldChoice("categorical_diff", "categorical_diff", "枚举值差异"),
    FieldChoice("missing_in_source", "missing_in_source", "某来源缺失"),
    FieldChoice("temporal_lag", "temporal_lag", "时间错位"),
    FieldChoice("unit_mismatch", "unit_mismatch", "单位 / 口径不一致"),
]

_PROVIDER_NEXT = [
    FieldChoice("rework", "rework", "返工后重测"),
    FieldChoice("reject", "reject", "驳回"),
    FieldChoice("escalate", "escalate", "升级处理"),
]

TEMPLATES: dict[IssueType, IssueTemplate] = {
    IssueType.DATA_SOURCE: IssueTemplate(
        intro=(
            "> **数据源接入 issue**：先说明用途、目标标准表、目标字段、更新频率、"
            "认证方式、样例、质量风险和验收标准。**禁止在 issue 中保存 Cookie / token / 密码。**"
        ),
        fields=[
            TemplateField("purpose", "用途（一句话）"),
            TemplateField("target_table", "目标标准表（如 instruments / prices_daily）"),
            TemplateField("target_fields", "目标字段（逗号分隔或多行说明）", "long_text"),
            TemplateField("source_category", "数据源用途分类", "choice", _FREQ_PURPOSE),
            TemplateField("update_frequency", "更新频率（如 T+1 09:00 UTC+8）"),
            TemplateField(
                "auth_method",
                "认证方式",
                "choice",
                _AUTH_METHODS,
                help="只描述方式，不写凭据；凭据走本地环境/密钥管理。",
            ),
            TemplateField("sample", "样例（来源、URL/文件、字段示例）", "long_text"),
            TemplateField("quality_risks", "质量风险", "long_text"),
            TemplateField("duplication_policy", "重复 / 多源策略", "choice", _DUP_POLICY),
            TemplateField("conflict_handling", "冲突时的默认处理", "choice", _CONFLICT_HANDLING),
            TemplateField("notes", "备注（可留空）", "long_text", required=False),
        ],
    ),
    IssueType.FACTOR: IssueTemplate(
        intro=(
            "> **因子 issue**：覆盖新增/修改/删除/禁用/评估/复活/淘汰/组合权重建议。"
            "声明假设、依赖字段、方向、参数、标准化建议与验收指标。"
        ),
        fields=[
            TemplateField("subaction", "动作", "choice", _FACTOR_SUBACTION),
            TemplateField("factor_name", "因子名称"),
            TemplateField("assumption", "假设 / 经济学逻辑", "long_text"),
            TemplateField("applicable_markets", "适用市场（逗号分隔）"),
            TemplateField("applicable_instruments", "适用品种（逗号分隔）"),
            TemplateField("timescale", "时间尺度", "choice", _TIMESCALES),
            TemplateField("dependencies", "依赖字段（逐项列出）", "long_text"),
            TemplateField("output_frequency", "输出频率", "choice", _TIMESCALES),
            TemplateField("direction", "方向", "choice", _DIRECTIONS),
            TemplateField("params", "参数（默认值与范围）", "long_text", required=False),
            TemplateField(
                "standardization", "标准化建议（可多选）", "multi_choice", _STANDARDIZATION
            ),
            TemplateField("missing_handling", "缺失值处理", "choice", _MISSING),
            TemplateField("ai_generated", "由 AI 生成候选？", "bool"),
            TemplateField("evaluation_metric", "验收指标（IC / 分组收益 / 衰减等）", "long_text"),
        ],
    ),
    IssueType.MARKET_RULE: IssueTemplate(
        intro=(
            "> **市场规则 issue**：说明交易日历、交易时段和市场规则配置。"
            "**第一个市场规则也必须经 issue 验收并人工确认后才能晋升。**"
        ),
        fields=[
            TemplateField("market", "市场（如 CN-A / HK / US-EQ / CN-Future）"),
            TemplateField("instrument_class", "品种（stock / etf / fund / future / option）"),
            TemplateField("calendar_source", "交易日历来源"),
            TemplateField("sessions", "sessions 定义（含夜盘/午休）", "long_text"),
            TemplateField("session_anchor", "session 归属交易日规则", "long_text"),
            TemplateField("t_plus", "T+ 规则", "choice", _T_PLUS),
            TemplateField("min_unit", "最小交易单位（含碎股规则）"),
            TemplateField("price_limit", "涨跌停 / 一字板规则", "long_text"),
            TemplateField("suspension_handling", "停牌 / ST 处理", "long_text"),
            TemplateField("night_session", "是否含夜盘", "bool"),
            TemplateField("holidays_source", "节假日来源"),
            TemplateField("fees", "手续费规则", "long_text"),
            TemplateField("taxes", "税费规则", "long_text"),
            TemplateField("settlement", "结算规则"),
            TemplateField("price_anchor", "价格锚点（实盘建议窗口）", "choice", _PRICE_ANCHORS),
            TemplateField("backtest_anchor", "回测执行锚点", "choice", _PRICE_ANCHORS),
            TemplateField("live_window", "实盘建议执行窗口", "long_text"),
            TemplateField(
                "sample_tests",
                "样例测试（节假日 / 夜盘 / T+ / 费用 / smoke 回测）",
                "long_text",
            ),
        ],
    ),
    IssueType.CLI: IssueTemplate(
        intro="> **控制台交互改进 issue**：说明要改哪个命令、要加什么子命令、交互问题怎么问，以及兼容性影响。",
        fields=[
            TemplateField("command_family", "目标命令", "choice", _CLI_FAMILIES),
            TemplateField("proposed_subcommand", "建议子命令（如 `issue triage`）"),
            TemplateField("question_tree", "交互问题树（节点+选项）", "long_text"),
            TemplateField("compatibility", "兼容性（参数 / 退出码 / 文件格式）"),
            TemplateField("breaking_change", "是否破坏性变更", "bool"),
        ],
    ),
    IssueType.MEMORY: IssueTemplate(
        intro=(
            "> **记忆入库 issue**：候选内容先进入待确认区，再由人工确认入库；**严禁包含密钥**。"
            "外部框架升级仅作提醒，迁移脚本走本 issue。"
        ),
        fields=[
            TemplateField("subaction", "动作", "choice", _MEMORY_SUBACTION),
            TemplateField("mem0_version_pin", "当前外部记忆框架版本（pin）"),
            TemplateField(
                "contains_secret",
                "是否含密钥（必须为 False）",
                "bool",
                help="若实际含密钥，请先脱敏；本字段不允许 True。",
            ),
            TemplateField("buffer_path", "待确认区路径（可留空）", required=False),
            TemplateField("downgrade_plan", "降级模式预案（可留空）", "long_text", required=False),
        ],
    ),
    IssueType.BUG: IssueTemplate(
        intro="> **Bug 报告**：复现步骤、期望、实际、影响、相关 run。",
        fields=[
            TemplateField("repro_steps", "复现步骤（按序号）", "long_text"),
            TemplateField("expected", "期望行为", "long_text"),
            TemplateField("actual", "实际行为", "long_text"),
            TemplateField("impact_scope", "影响范围", "choice", _BUG_SCOPE),
            TemplateField("related_run", "相关 run 目录（可留空）", required=False),
            TemplateField("blocking", "是否阻塞日常流程", "bool"),
        ],
    ),
    IssueType.FRAMEWORK: IssueTemplate(
        intro="> **框架改进 issue**：模块边界、命名、性能、可测试性、约束索引。",
        fields=[
            TemplateField("module", "目标模块", "choice", _FRAMEWORK_MODULES),
            TemplateField("boundary_change", "是否调整模块边界 / 公共 API", "bool"),
            TemplateField("naming", "命名调整（可留空）", required=False),
            TemplateField("performance_target", "性能目标（可留空）", required=False),
            TemplateField("testability", "可测试性变化（可留空）", "long_text", required=False),
            TemplateField("constraint_index", "涉及约束索引（ARCHITECTURE 段落）", "long_text"),
        ],
    ),
    IssueType.STRATEGY_PROFILE: IssueTemplate(
        intro=(
            "> **策略配置 issue**：声明市场/品种/时间尺度/数据频率/universe/因子/"
            "标准化链/交易规则/执行假设/调仓频率/持仓期/仓位约束/费用滑点/风控/"
            "价格偏差阈值/生效状态。**策略配置不允许自动填充未确认值。**"
        ),
        fields=[
            TemplateField("profile_name", "策略配置名称"),
            TemplateField("market", "市场"),
            TemplateField("instruments", "品种"),
            TemplateField("timescale", "时间尺度", "choice", _TIMESCALES),
            TemplateField("data_frequency", "数据频率", "choice", _TIMESCALES),
            TemplateField("universe", "universe 定义（含历史 universe 规则）", "long_text"),
            TemplateField("factors", "因子清单（含权重建议）", "long_text"),
            TemplateField("standardization_chain", "标准化链（顺序）", "long_text"),
            TemplateField("trading_rules", "交易规则（依赖市场规则配置）", "long_text"),
            TemplateField("execution_assumption", "执行假设", "long_text"),
            TemplateField("rebalance_frequency", "调仓频率"),
            TemplateField("holding_period", "持仓期"),
            TemplateField(
                "position_constraint", "仓位约束（Top N / 单票上限 / 现金等）", "long_text"
            ),
            TemplateField("fees_slippage", "费用 / 滑点设定", "long_text"),
            TemplateField("risk_control", "风控规则", "long_text"),
            TemplateField("price_deviation_threshold", "价格偏差阈值"),
            TemplateField("inheritance_parent", "继承的父级策略配置（可留空）", required=False),
            TemplateField("effective", "本 issue 通过后是否立即生效", "bool"),
        ],
    ),
    IssueType.PORTFOLIO: IssueTemplate(
        intro=(
            "> **持仓 / 调仓 issue**：调仓计划、执行差异、价格偏差、计划外交易、"
            "重新初始化、旧计划核对。"
        ),
        fields=[
            TemplateField("subaction", "动作", "choice", _PORTFOLIO_SUBACTION),
            TemplateField("account", "账户标识"),
            TemplateField("snapshot_summary", "快照摘要（可留空）", "long_text", required=False),
            TemplateField("target_holdings", "目标持仓（可留空）", "long_text", required=False),
            TemplateField(
                "suggested_trades", "建议交易（数量 / 价格）", "long_text", required=False
            ),
            TemplateField("effective_window", "生效窗口（可留空）", required=False),
            TemplateField("discrepancy_note", "差异说明 / 触发原因", "long_text", required=False),
            TemplateField("reattribution_breakpoint", "是否产生归因断点", "bool"),
        ],
    ),
    IssueType.PROVIDER_ACCEPTANCE_FAIL: IssueTemplate(
        intro=(
            "> **数据源验收失败 issue**：契约测试、字段映射差异、manifest 异常、smoke 回测失败。"
        ),
        fields=[
            TemplateField("provider", "数据源名称"),
            TemplateField("contract_test_result", "契约测试结果（含失败用例）", "long_text"),
            TemplateField("field_mapping_diff", "字段映射差异", "long_text"),
            TemplateField("manifest_anomaly", "manifest 异常（来源/版本/口径）", "long_text"),
            TemplateField("smoke_backtest_result", "smoke 回测结果", "long_text"),
            TemplateField("next_action", "下一步处理", "choice", _PROVIDER_NEXT),
        ],
    ),
    IssueType.DATA_QUALITY_CONFLICT: IssueTemplate(
        intro=("> **数据质量冲突 issue**：多源字段冲突；合并规则建议、影响范围、回溯方案。"),
        fields=[
            TemplateField("standard_field", "受影响标准字段"),
            TemplateField("sources", "涉及来源（逐项列出）", "long_text"),
            TemplateField("conflict_pattern", "冲突类型", "choice", _CONFLICT_PATTERN),
            TemplateField("proposed_merge_rule", "建议合并规则（不自动覆盖）", "long_text"),
            TemplateField("impact_window", "影响时间窗口（如 2024-01~2024-06）"),
            TemplateField("backfill_plan", "回溯 / 修复方案", "long_text"),
        ],
    ),
}

_BASE_BODY = """## 背景

{background}

## 目标

{goal}

## 范围

{scope}

## 验收标准

{acceptance}

## 风险

{risks}
"""

_SOLUTION_SECTION = """## 解决摘要

{resolution_summary}
"""

_PLACEHOLDER = "_(待补充)_"

def _format_value(field_obj: TemplateField, value: Any) -> str:
    
    if value is None or value == "":
        return _PLACEHOLDER
    if field_obj.kind == "bool":
        return "是" if bool(value) else "否"
    if field_obj.kind == "multi_choice":
        if isinstance(value, list):
            return ", ".join(str(v) for v in value) if value else _PLACEHOLDER
        return str(value)
    return str(value)

def render_template(
    issue_type: IssueType,
    *,
    base: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    
    base = base or {}
    extra = extra or {}
    tpl = TEMPLATES[issue_type]

    safe_base = {
        "background": base.get("background") or _PLACEHOLDER,
        "goal": base.get("goal") or _PLACEHOLDER,
        "scope": base.get("scope") or _PLACEHOLDER,
        "acceptance": base.get("acceptance") or _PLACEHOLDER,
        "risks": base.get("risks") or _PLACEHOLDER,
    }
    body = f"{tpl.intro}\n\n" + _BASE_BODY.format(**safe_base)

    if tpl.fields:
        body += "\n## 类型特有字段\n\n"
        for f in tpl.fields:
            section_title = f.section or f.label
            value = _format_value(f, extra.get(f.key))
            body += f"### {section_title}\n\n_key: `{f.key}`_\n\n{value}\n\n"

    body += _SOLUTION_SECTION.format(
        resolution_summary=base.get("resolution_summary") or _PLACEHOLDER,
    )
    return body

def required_field_keys(issue_type: IssueType) -> list[str]:
    
    return [f.key for f in TEMPLATES[issue_type].fields if f.required]
