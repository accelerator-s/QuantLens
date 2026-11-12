from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from paths import workspace
from providers.exceptions import ProviderDataError

from .fundamentals import _classify_board
from .tables import HOLDER_TABLES

QUALITY_HOLDERS_REPORT_FILENAME = "quality_holders_missing_ann_date.parquet"
CLASSIFICATION_RULES_FILENAME = "shareholder_classification.yaml"

_MARKET = "CN-A"
_INSTRUMENT_CLASS = "stock"

UNKNOWN_HOLDER_TYPE = "unknown"

_NAME_TABLES = ("top_shareholders", "top_float_shareholders")

# 质量报告缺口类别
GAP_NON_STANDARD_PERIOD = "non_standard_period"  
GAP_MISSING_DISCLOSURE = "missing_disclosure"  # 季末报告期但披露表无记录

_PERIOD_ENDS = ("03-31", "06-30", "09-30", "12-31")

_REPORT_COLUMNS = [
    "instrument_id",
    "report_period",
    "table",
    "gap_category",
    "year",
    "board",
]

def _staged_dir() -> Path:
    return workspace().staged_for(_MARKET, _INSTRUMENT_CLASS)

def holders_quality_report_path() -> Path:
    
    return _staged_dir() / QUALITY_HOLDERS_REPORT_FILENAME

def classification_rules_path() -> Path:
    
    return workspace().config / CLASSIFICATION_RULES_FILENAME

@dataclass(frozen=True)
class HolderRule:

    rule_id: str
    holder_type: str
    pattern: re.Pattern[str]

def load_classification_rules(path: Path | None = None) -> list[HolderRule]:
    
    import yaml

    rules_path = path or classification_rules_path()
    if not rules_path.exists():
        raise ProviderDataError(
            code="PROVIDER_HOLDER_RULES_MISSING",
            message=f"股东类型分类规则表不存在：{rules_path}。",
            blocking=True,
            suggested_action=(
                "config/shareholder_classification.yaml 随仓库提交，"
                "请检查工作区完整性或从 git 恢复该文件。"
            ),
        )

    def _invalid(detail: str) -> ProviderDataError:
        return ProviderDataError(
            code="PROVIDER_HOLDER_RULES_INVALID",
            message=f"股东类型分类规则表格式错误：{detail}",
            context={"path": str(rules_path)},
            blocking=True,
            suggested_action=(
                "按 config/shareholder_classification.yaml 头部注释的维护说明修正后重试。"
            ),
        )

    try:
        raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as e:
        raise _invalid(f"无法读取或解析 YAML（{e.__class__.__name__}）。") from e

    if not isinstance(raw, dict) or "types" not in raw or "rules" not in raw:
        raise _invalid("须为包含 types 与 rules 两个键的映射。")

    raw_types = raw["types"]
    raw_rules = raw["rules"]
    if not isinstance(raw_types, list):
        raise _invalid("types 必须是列表。")
    if not isinstance(raw_rules, list):
        raise _invalid("rules 必须是列表。")

    if not raw_types or any(not isinstance(item, dict) for item in raw_types):
        raise _invalid("types 中每一项须为包含 id 的映射。")
    declared_type_ids = [item.get("id") for item in raw_types]
    invalid_type_id = any(
        not isinstance(type_id, str) or not type_id.strip() for type_id in declared_type_ids
    )
    if invalid_type_id:
        raise _invalid("types 中每一项须包含非空字符串 id。")
    type_ids = set(declared_type_ids)
    if len(type_ids) != len(declared_type_ids):
        raise _invalid("types 中存在重复 id。")
    if UNKNOWN_HOLDER_TYPE in type_ids:
        raise _invalid(f"types 不应声明保留类型 {UNKNOWN_HOLDER_TYPE}（未命中时自动使用）。")

    rules: list[HolderRule] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw_rules, 1):
        if not isinstance(item, dict):
            raise _invalid(f"第 {i} 条规则不是映射。")
        rule_id = item.get("id")
        holder_type = item.get("type")
        pattern = item.get("pattern")
        fields = {"id": rule_id, "type": holder_type, "pattern": pattern}
        invalid_fields = [
            name
            for name, value in fields.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if invalid_fields:
            raise _invalid(f"第 {i} 条规则的 {', '.join(invalid_fields)} 须为非空字符串。")
        rule_id = rule_id.strip()
        holder_type = holder_type.strip()
        pattern = pattern.strip()
        if rule_id in seen_ids:
            raise _invalid(f"规则 id 重复：{rule_id}。")
        seen_ids.add(rule_id)
        if holder_type not in type_ids:
            raise _invalid(f"规则 {rule_id} 的 type={holder_type} 未在 types 中声明。")
        try:
            compiled = re.compile(pattern)
        except (re.error, TypeError) as e:
            raise _invalid(f"规则 {rule_id} 的正则无法编译：{e}。") from e
        rules.append(HolderRule(rule_id=rule_id, holder_type=holder_type, pattern=compiled))

    if not rules:
        raise _invalid("rules 为空。")
    return rules

def classify_shareholders(
    names: pd.Series,
    rules: list[HolderRule],
) -> tuple[pd.Series, pd.Series]:

    cleaned = names.fillna("").astype(str).str.replace("[\\s\u3000]+", "", regex=True)

    holder_type = pd.Series(UNKNOWN_HOLDER_TYPE, index=names.index, dtype="object")
    rule_hit = pd.Series(pd.NA, index=names.index, dtype="object")
    unresolved = cleaned != ""  

    for rule in rules:
        if not unresolved.any():
            break
        hit = unresolved & cleaned.str.contains(rule.pattern, na=False)
        if hit.any():
            holder_type[hit] = rule.holder_type
            rule_hit[hit] = rule.rule_id
            unresolved &= ~hit

    return holder_type, rule_hit

def _gap_categories(report_period: pd.Series) -> pd.Series:
    
    is_quarter_end = report_period.str[5:].isin(_PERIOD_ENDS)
    return pd.Series(GAP_MISSING_DISCLOSURE, index=report_period.index, dtype="object").where(
        is_quarter_end, GAP_NON_STANDARD_PERIOD
    )

def attach_holder_data() -> dict:
    
    staged = _staged_dir()
    disc_path = staged / "disclosure_dates.parquet"
    if not disc_path.exists():
        raise ProviderDataError(
            code="PROVIDER_DISCLOSURE_DATES_MISSING",
            message="staged 中不存在 disclosure_dates 表，无法关联机构持仓披露日。",
            blocking=True,
            suggested_action="先执行 data update 下载披露日期表后重试。",
        )

    rules = load_classification_rules()

    disc = pd.read_parquet(disc_path)
    ann_map = (
        disc.dropna(subset=["ann_date"])
        .groupby(["instrument_id", "report_period"], as_index=False)["ann_date"]
        .min()
    )

    summary: dict[str, dict] = {}
    report_frames: list[pd.DataFrame] = []

    for table in HOLDER_TABLES:
        path = staged / f"{table}.parquet"
        if not path.exists():
            summary[table] = {
                "status": "not_downloaded",
                "rows": 0,
                "missing_ann_date": 0,
                "unknown_holder_names": 0,
            }
            continue

        df = pd.read_parquet(path)
        df = df.drop(columns=["ann_date", "holder_type", "holder_type_rule"], errors="ignore")
        df = df.merge(ann_map, on=["instrument_id", "report_period"], how="left")

        entry: dict = {"status": "ok", "rows": len(df)}

        if table in _NAME_TABLES:
            holder_type, rule_hit = classify_shareholders(df["shareholder_name"], rules)
            df["holder_type"] = holder_type
            df["holder_type_rule"] = rule_hit
            entry["unknown_holder_names"] = int((holder_type == UNKNOWN_HOLDER_TYPE).sum())
            entry["holder_type_counts"] = holder_type.value_counts().to_dict()
        else:
            entry["unknown_holder_names"] = 0

        missing_mask = df["ann_date"].isna()
        n_missing = int(missing_mask.sum())
        entry["missing_ann_date"] = n_missing
        if n_missing:
            detail = df.loc[missing_mask, ["instrument_id", "report_period"]].copy()
            detail["table"] = table
            detail["gap_category"] = _gap_categories(detail["report_period"])
            detail["year"] = detail["report_period"].str[:4]
            detail["board"] = _classify_board(detail["instrument_id"])
            report_frames.append(detail[_REPORT_COLUMNS])

        df.to_parquet(path, index=False)
        summary[table] = entry

    if report_frames:
        report = pd.concat(report_frames, ignore_index=True)
    else:
        report = pd.DataFrame(columns=_REPORT_COLUMNS)

    report_path = holders_quality_report_path()
    report.to_parquet(report_path, index=False)

    pairs = report.drop_duplicates(subset=["instrument_id", "report_period", "table"])
    return {
        "tables": summary,
        "report_path": str(report_path),
        "report_rows": len(report),
        "unique_missing_pairs": len(pairs),
        "categories": pairs["gap_category"].value_counts().to_dict() if len(pairs) else {},
        "rules_count": len(rules),
    }
