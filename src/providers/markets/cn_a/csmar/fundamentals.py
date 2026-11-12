from __future__ import annotations

from pathlib import Path

import pandas as pd

from market_rules.registry import MarketRuleRegistry
from paths import workspace
from providers.exceptions import ProviderDataError

from .tables import FUNDAMENTAL_TABLES

QUALITY_REPORT_FILENAME = "quality_missing_ann_date.parquet"

_MARKET = "CN-A"
_INSTRUMENT_CLASS = "stock"

IMPUTED_LISTING = "listing_fallback"
IMPUTED_STATUTORY = "statutory_deadline_fallback"

# 质量报告缺口类别
GAP_PRE_LISTING = "pre_listing"
GAP_IN_TRADING = "in_trading"
GAP_NO_PRICE_HISTORY = "no_price_history"

_REPORT_COLUMNS = [
    "instrument_id",
    "report_period",
    "statement_type",
    "table",
    "gap_category",
    "year",
    "board",
    "effective_date",
    "ann_date_imputed",
]

def _staged_dir() -> Path:
    return workspace().staged_for(_MARKET, _INSTRUMENT_CLASS)

def quality_report_path() -> Path:
    
    return _staged_dir() / QUALITY_REPORT_FILENAME

def _classify_board(codes: pd.Series) -> pd.Series:
    
    board = pd.Series("其他", index=codes.index, dtype="object")
    p2 = codes.str[:2]
    p3 = codes.str[:3]
    board[p2 == "60"] = "沪主板"
    board[p3.isin(["000", "001", "002", "003"])] = "深主板"
    board[p3.isin(["300", "301", "302"])] = "创业板"
    board[p3.isin(["688", "689"])] = "科创板"
    board[p2.isin(["43", "83", "87", "88", "92"])] = "北交所/新三板"
    board[p2.isin(["40", "42"])] = "老三板"
    return board

def _first_trade_dates(staged: Path) -> pd.Series:
    
    path = staged / "prices_daily.parquet"
    if not path.exists():
        raise ProviderDataError(
            code="PROVIDER_PRICES_DAILY_MISSING",
            message="staged 中不存在 prices_daily 表，无法确定首个交易日，effective_date 回退无法执行。",
            blocking=True,
            suggested_action="先执行 data update 下载行情数据后重试。",
        )
    px = pd.read_parquet(path, columns=["instrument_id", "date"])
    return px.groupby("instrument_id")["date"].min()

def disclosure_deadline_maps() -> tuple[dict[str, str], dict[str, int]]:
    
    rule = MarketRuleRegistry().resolve(_MARKET, _INSTRUMENT_CLASS)
    schedule = rule.disclosure_deadlines
    if not schedule.items:
        raise ProviderDataError(
            code="PROVIDER_DISCLOSURE_DEADLINES_UNCONFIGURED",
            message=(
                f"市场规则 {_MARKET}/{_INSTRUMENT_CLASS} 未配置法定披露截止日"
                "（disclosure_deadlines），无法执行 effective_date 回退。"
            ),
            blocking=True,
            suggested_action=(
                "在 config/market_rules/CN-A-stock.yaml 中补充 disclosure_deadlines 规则表后重试。"
            ),
        )
    deadline_map = {r.period_end: r.deadline for r in schedule.items}
    offset_map = {r.period_end: r.year_offset for r in schedule.items}
    return deadline_map, offset_map

def _statutory_deadlines(
    report_period: pd.Series,
    deadline_map: dict[str, str],
    offset_map: dict[str, int],
) -> pd.Series:
    
    mmdd = report_period.str[5:]
    unknown = sorted(set(mmdd.unique()) - set(deadline_map))
    if unknown:
        raise ProviderDataError(
            code="PROVIDER_UNKNOWN_PERIOD_END",
            message=f"报告期期末 {unknown} 未配置法定披露截止日。",
            context={"unknown_period_ends": unknown},
            blocking=True,
            suggested_action="在市场规则 disclosure_deadlines 中补充对应报告期期末的截止日规则。",
        )
    year = report_period.str[:4].astype(int) + mmdd.map(offset_map)
    return year.astype(str) + "-" + mmdd.map(deadline_map)

def _apply_effective_date(
    df: pd.DataFrame,
    first_dates: pd.Series,
    deadline_map: dict[str, str],
    offset_map: dict[str, int],
) -> tuple[pd.DataFrame, pd.Series]:
    
    ftd = df["instrument_id"].map(first_dates)
    
    ftd_filled = ftd.fillna("")
    has_ann = df["ann_date"].notna()

    effective = pd.Series(pd.NA, index=df.index, dtype="object")
    imputed = pd.Series(pd.NA, index=df.index, dtype="object")
    category = pd.Series(pd.NA, index=df.index, dtype="object")

    ann = df.loc[has_ann, "ann_date"]
    listed = ftd_filled[has_ann]
    effective[has_ann] = ann.where(ann >= listed, listed)

    missing = ~has_ann
    pre_listing = missing & ftd.notna() & (df["report_period"] < ftd_filled)
    in_trading = missing & ftd.notna() & (df["report_period"] >= ftd_filled)
    no_history = missing & ftd.isna()

    effective[pre_listing] = ftd[pre_listing]
    imputed[pre_listing] = IMPUTED_LISTING
    category[pre_listing] = GAP_PRE_LISTING

    statutory = in_trading | no_history
    if statutory.any():
        effective[statutory] = _statutory_deadlines(
            df.loc[statutory, "report_period"], deadline_map, offset_map
        )
        imputed[statutory] = IMPUTED_STATUTORY
    category[in_trading] = GAP_IN_TRADING
    category[no_history] = GAP_NO_PRICE_HISTORY

    if effective.isna().any():
        n = int(effective.isna().sum())
        raise ProviderDataError(
            code="PROVIDER_EFFECTIVE_DATE_INCOMPLETE",
            message=f"effective_date 回退后仍有 {n} 条记录为空，分级规则未覆盖全部情形。",
            blocking=True,
            suggested_action="检查 report_period 与行情首日数据质量后重试。",
        )

    df["effective_date"] = effective
    df["ann_date_imputed"] = imputed
    return df, category

def attach_ann_dates() -> dict:
    
    staged = _staged_dir()
    disc_path = staged / "disclosure_dates.parquet"
    if not disc_path.exists():
        raise ProviderDataError(
            code="PROVIDER_DISCLOSURE_DATES_MISSING",
            message="staged 中不存在 disclosure_dates 表，无法关联公告日。",
            blocking=True,
            suggested_action="先执行 data update --table disclosure_dates 下载披露日期表。",
        )

    disc = pd.read_parquet(disc_path)
    ann_map = (
        disc.dropna(subset=["ann_date"])
        .groupby(["instrument_id", "report_period"], as_index=False)["ann_date"]
        .min()
    )

    first_dates = _first_trade_dates(staged)
    deadline_map, offset_map = disclosure_deadline_maps()

    summary: dict[str, dict] = {}
    report_frames: list[pd.DataFrame] = []

    for table in FUNDAMENTAL_TABLES:
        path = staged / f"{table}.parquet"
        if not path.exists():
            summary[table] = {
                "status": "not_downloaded",
                "rows": 0,
                "missing_ann_date": 0,
                "imputed_listing": 0,
                "imputed_statutory": 0,
            }
            continue

        df = pd.read_parquet(path)
        df = df.drop(columns=["ann_date", "effective_date", "ann_date_imputed"], errors="ignore")
        df = df.merge(ann_map, on=["instrument_id", "report_period"], how="left")

        df, category = _apply_effective_date(df, first_dates, deadline_map, offset_map)

        missing_mask = df["ann_date"].isna()
        n_missing = int(missing_mask.sum())
        if n_missing:
            detail = df.loc[
                missing_mask,
                [
                    "instrument_id",
                    "report_period",
                    "statement_type",
                    "effective_date",
                    "ann_date_imputed",
                ],
            ].copy()
            detail["table"] = table
            detail["gap_category"] = category[missing_mask]
            detail["year"] = detail["report_period"].str[:4]
            detail["board"] = _classify_board(detail["instrument_id"])
            report_frames.append(detail[_REPORT_COLUMNS])

        df.to_parquet(path, index=False)
        summary[table] = {
            "status": "ok",
            "rows": len(df),
            "missing_ann_date": n_missing,
            "imputed_listing": int((df["ann_date_imputed"] == IMPUTED_LISTING).sum()),
            "imputed_statutory": int((df["ann_date_imputed"] == IMPUTED_STATUTORY).sum()),
        }

    if report_frames:
        report = pd.concat(report_frames, ignore_index=True)
    else:
        report = pd.DataFrame(columns=_REPORT_COLUMNS)

    report_path = quality_report_path()
    report.to_parquet(report_path, index=False)

    pairs = report.drop_duplicates(subset=["instrument_id", "report_period"])
    return {
        "tables": summary,
        "report_path": str(report_path),
        "report_rows": len(report),
        "unique_missing_pairs": len(pairs),
        "categories": pairs["gap_category"].value_counts().to_dict() if len(pairs) else {},
    }
