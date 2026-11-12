from __future__ import annotations

from pathlib import Path

import pandas as pd

from paths import workspace

from .tables import ANALYST_TABLES

QUALITY_ANALYST_REPORT_FILENAME = "quality_analyst_data.parquet"

FORECAST_UNIQUE_KEY = (
    "instrument_id",
    "institution_id",
    "publish_date",
    "forecast_period",
    "report_id",
    "analyst_key",
)
FORECAST_UNIQUE_KEY_TEXT = " × ".join(FORECAST_UNIQUE_KEY)
CSMAR_SOURCE_VERIFICATION = (
    "CSMAR 分析师预测库目录 getSingleTableLeftTree/1（2026-08-08）："
    "表 294-304、7345-7346，无可按 ReportID 关联的报告主表"
)

_MARKET = "CN-A"
_INSTRUMENT_CLASS = "stock"
_REPORT_KEY = ["instrument_id", "report_id"]
_METADATA_COLUMNS = [
    "institution_id",
    "institution_name",
    "analyst_id",
    "analyst_name",
]
_QUALITY_COLUMNS = [
    "table",
    "gap_category",
    "instrument_id",
    "report_id",
    "report_date",
    "publish_date",
    "institution_id",
    "forecast_year",
    "forecast_period",
    "analyst_id",
    "analyst_name",
    "analyst_key",
    "source_record_key",
    "quality_status",
    "conflict_class",
    "authoritative_source",
    "resolution",
    "pit_eligible",
    "consensus_eligible",
    "related_report_count",
    "related_analyst_count",
    "related_forecast_period_count",
    "recommended_unique_key",
]

def _staged_dir() -> Path:
    return workspace().staged_for(_MARKET, _INSTRUMENT_CLASS)

def analyst_quality_report_path() -> Path:
    
    return _staged_dir() / QUALITY_ANALYST_REPORT_FILENAME

def _load_tables() -> dict[str, pd.DataFrame]:
    
    staged = _staged_dir()
    return {
        table: pd.read_parquet(staged / f"{table}.parquet")
        for table in ANALYST_TABLES
        if (staged / f"{table}.parquet").exists()
    }

def _build_publish_lookup(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    
    frames: list[pd.DataFrame] = []
    for df in tables.values():
        if not set(_REPORT_KEY + ["publish_date"]).issubset(df.columns):
            continue
        available = df.dropna(subset=_REPORT_KEY + ["publish_date"])[_REPORT_KEY + ["publish_date"]]
        frames.append(available)
    if not frames:
        return pd.DataFrame(columns=_REPORT_KEY + ["linked_publish_date"])

    combined = pd.concat(frames, ignore_index=True)
    combined["publish_date"] = pd.to_datetime(
        combined["publish_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    return (
        combined.dropna(subset=["publish_date"])
        .groupby(_REPORT_KEY, as_index=False)["publish_date"]
        .min()
        .rename(columns={"publish_date": "linked_publish_date"})
    )

def _build_metadata_lookup(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    
    frames: list[pd.DataFrame] = []
    for table in ("analyst_forecast", "analyst_rating"):
        df = tables.get(table)
        if df is None or not set(_REPORT_KEY).issubset(df.columns):
            continue
        cols = _REPORT_KEY + [col for col in _METADATA_COLUMNS if col in df.columns]
        frames.append(df[cols])
    if not frames:
        return pd.DataFrame(columns=_REPORT_KEY + _METADATA_COLUMNS)

    metadata = pd.concat(frames, ignore_index=True)
    for col in _METADATA_COLUMNS:
        if col not in metadata.columns:
            metadata[col] = pd.NA
    metadata = metadata.dropna(subset=_REPORT_KEY)

    for col in _METADATA_COLUMNS:
        metadata[col] = metadata[col].mask(metadata[col].astype("string").str.strip().eq(""), pd.NA)
    return metadata.groupby(_REPORT_KEY, as_index=False, sort=True)[_METADATA_COLUMNS].first()

def _quality_rows(
    df: pd.DataFrame,
    table: str,
    mask: pd.Series,
    category: str,
    **annotations: object,
) -> pd.DataFrame:
    
    detail = pd.DataFrame(index=df.index[mask])
    detail["table"] = table
    detail["gap_category"] = category
    for col in _QUALITY_COLUMNS[2:]:
        detail[col] = df.loc[mask, col] if col in df.columns else pd.NA
    for col, value in annotations.items():
        if col not in _QUALITY_COLUMNS:
            continue
        detail[col] = value.loc[detail.index] if isinstance(value, pd.Series) else value
    return detail[_QUALITY_COLUMNS].reset_index(drop=True)

def _blank(series: pd.Series) -> pd.Series:
    
    return series.isna() | series.astype("string").str.strip().eq("")

def _empty_export_artifact_mask(df: pd.DataFrame) -> pd.Series:
    
    ignored_columns = {
        "instrument_id",
        "source_record_key",
        "downloaded_at",
        "analyst_key",
        "forecast_year",
        "publish_date_source",
    }
    business_columns = [col for col in df.columns if col not in ignored_columns]
    if not business_columns:
        return pd.Series(False, index=df.index, dtype="bool")
    numeric_code = df["instrument_id"].astype("string").str.fullmatch(r"\d{1,6}", na=False)
    artifact_candidate_mask = numeric_code & _blank(df["report_id"])
    result = pd.Series(False, index=df.index, dtype="bool")
    if not artifact_candidate_mask.any():
        return result
    candidate_business = df.loc[artifact_candidate_mask, business_columns]
    blank_business = (
        candidate_business.isna()
        | candidate_business.astype("string").apply(lambda column: column.str.strip().eq(""))
    ).fillna(True)
    result.loc[artifact_candidate_mask] = blank_business.all(axis=1)
    return result

def _classify_forecast_versions(df: pd.DataFrame, duplicate_mask: pd.Series) -> pd.DataFrame:
    
    business_key = [
        "instrument_id",
        "institution_id",
        "publish_date",
        "forecast_year",
    ]
    versions = df.loc[duplicate_mask].copy()
    grouped = versions.groupby(business_key, dropna=False)
    versions["related_report_count"] = grouped["report_id"].transform("nunique")
    versions["related_analyst_count"] = grouped["analyst_key"].transform("nunique")
    versions["related_forecast_period_count"] = grouped["forecast_period"].transform("nunique")

    period_diff = versions["related_forecast_period_count"].gt(1)
    report_diff = versions["related_report_count"].gt(1)
    analyst_diff = versions["related_analyst_count"].gt(1)
    versions["conflict_class"] = "unexplained_same_identity"
    versions.loc[analyst_diff, "conflict_class"] = "same_report_different_analyst"
    versions.loc[report_diff, "conflict_class"] = "different_report_same_analyst"
    versions.loc[report_diff & analyst_diff, "conflict_class"] = "different_report_and_analyst"
    versions.loc[period_diff, "conflict_class"] = "different_forecast_period"
    versions["quality_status"] = "explained_distinct_version"
    versions.loc[versions["conflict_class"].eq("unexplained_same_identity"), "quality_status"] = (
        "unexplained_conflict"
    )
    versions["authoritative_source"] = "CSMAR 原始 ReportID、分析师标识与 Fenddt"
    versions["resolution"] = "保留全部报告版本；下游按推荐唯一键识别"
    versions["pit_eligible"] = True
    versions["consensus_eligible"] = True
    versions["recommended_unique_key"] = FORECAST_UNIQUE_KEY_TEXT
    versions["table"] = "analyst_forecast"
    versions["gap_category"] = "business_key_duplicate"
    return versions.reindex(columns=_QUALITY_COLUMNS).reset_index(drop=True)

def attach_analyst_data() -> dict:
    
    tables = _load_tables()
    publish_lookup = _build_publish_lookup(tables)
    metadata_lookup = _build_metadata_lookup(tables)
    summary: dict[str, dict] = {}
    quality_frames: list[pd.DataFrame] = []

    for table in ANALYST_TABLES:
        df = tables.get(table)
        if df is None:
            summary[table] = {
                "status": "not_downloaded",
                "rows": 0,
                "publish_date_filled": 0,
                "missing_publish_date": 0,
                "missing_report_id": 0,
                "business_key_duplicates": 0,
                "missing_report_metadata": 0,
                "excluded_empty_artifacts": 0,
            }
            continue

        empty_artifact_mask = _empty_export_artifact_mask(df)
        excluded_empty_artifacts = int(empty_artifact_mask.sum())
        if excluded_empty_artifacts:
            
            df = df.loc[~empty_artifact_mask].reset_index(drop=True)

        prior_source = (
            df["publish_date_source"]
            if "publish_date_source" in df.columns
            else pd.Series(pd.NA, index=df.index, dtype="object")
        )
        linked_before = prior_source.eq("report_link")
        if linked_before.any():
            df.loc[linked_before, "publish_date"] = pd.NA
        df = df.drop(columns=["publish_date_source"], errors="ignore")

        original_has_date = ~_blank(df["publish_date"])
        df = df.merge(publish_lookup, on=_REPORT_KEY, how="left")
        fill_mask = _blank(df["publish_date"]) & df["linked_publish_date"].notna()
        df.loc[fill_mask, "publish_date"] = df.loc[fill_mask, "linked_publish_date"]
        df["publish_date_source"] = "missing"
        df.loc[original_has_date, "publish_date_source"] = "csmar"
        df.loc[fill_mask, "publish_date_source"] = "report_link"
        df = df.drop(columns=["linked_publish_date"])

        empty_artifact_mask = _empty_export_artifact_mask(df)
        missing_metadata = 0
        if table == "analyst_target_price":
            df = df.drop(columns=_METADATA_COLUMNS, errors="ignore")
            df = df.merge(metadata_lookup, on=_REPORT_KEY, how="left")
            empty_artifact_mask = _empty_export_artifact_mask(df)
            missing_metadata_mask = _blank(df["institution_id"]) & ~empty_artifact_mask
            missing_metadata = int(missing_metadata_mask.sum())
            if missing_metadata:
                quality_frames.append(
                    _quality_rows(
                        df,
                        table,
                        missing_metadata_mask,
                        "missing_report_metadata",
                        quality_status="isolated_target_report",
                        conflict_class="target_report_not_in_detail_tables",
                        authoritative_source=(
                            "CSMAR AF_TARGETVALUE(301) ReportID 未出现在 "
                            "AF_Forecast(299)/AF_Bench(300)"
                        ),
                        resolution=("保留目标价与发布日期；机构维度一致预期排除，等待权威元数据"),
                        pit_eligible=True,
                        consensus_eligible=False,
                    )
                )

        missing_publish_mask = _blank(df["publish_date"]) & ~empty_artifact_mask
        if missing_publish_mask.any():
            quality_frames.append(
                _quality_rows(
                    df,
                    table,
                    missing_publish_mask,
                    "missing_publish_date",
                    quality_status="authoritative_source_unavailable",
                    conflict_class="source_publish_date_unavailable",
                    authoritative_source=CSMAR_SOURCE_VERIFICATION,
                    resolution="保持 publish_date 为空；PIT 与一致预期计算显式排除",
                    pit_eligible=False,
                    consensus_eligible=False,
                )
            )

        missing_report_id_mask = _blank(df["report_id"])
        if missing_report_id_mask.any():
            unresolved_id_mask = missing_report_id_mask & ~empty_artifact_mask
            if unresolved_id_mask.any():
                quality_frames.append(
                    _quality_rows(
                        df,
                        table,
                        unresolved_id_mask,
                        "missing_report_id",
                        quality_status="unresolved_missing_report_id",
                        conflict_class="source_report_id_unavailable",
                        authoritative_source="CSMAR 原始导出字段 ReportID",
                        resolution="保留原始记录；PIT 与一致预期计算显式排除",
                        pit_eligible=False,
                        consensus_eligible=False,
                    )
                )
        business_duplicates = 0
        if table == "analyst_forecast":
            business_key = [
                "instrument_id",
                "institution_id",
                "publish_date",
                "forecast_year",
            ]
            complete = df[business_key].notna().all(axis=1)
            duplicate_mask = complete & df.duplicated(subset=business_key, keep=False)
            business_duplicates = int(duplicate_mask.sum())
            if business_duplicates:
                quality_frames.append(_classify_forecast_versions(df, duplicate_mask))

        path = _staged_dir() / f"{table}.parquet"
        df.to_parquet(path, index=False)
        summary[table] = {
            "status": "ok",
            "rows": len(df),
            "publish_date_filled": int(fill_mask.sum()),
            "missing_publish_date": int(missing_publish_mask.sum()),
            "missing_report_id": int(missing_report_id_mask.sum()),
            "business_key_duplicates": business_duplicates,
            "missing_report_metadata": missing_metadata,
            "excluded_empty_artifacts": excluded_empty_artifacts,
        }

    quality = (
        pd.concat(quality_frames, ignore_index=True)
        if quality_frames
        else pd.DataFrame(columns=_QUALITY_COLUMNS)
    )
    quality_path = analyst_quality_report_path()
    quality_path.parent.mkdir(parents=True, exist_ok=True)
    quality.to_parquet(quality_path, index=False)

    categories = quality["gap_category"].value_counts().to_dict() if not quality.empty else {}
    statuses = quality["quality_status"].value_counts().to_dict() if not quality.empty else {}
    conflict_classes = (
        quality["conflict_class"].value_counts().to_dict() if not quality.empty else {}
    )
    unexplained_conflicts = int(quality["quality_status"].eq("unexplained_conflict").sum())
    pit_excluded = int(quality["pit_eligible"].eq(False).sum())
    consensus_excluded = int(quality["consensus_eligible"].eq(False).sum())
    return {
        "tables": summary,
        "report_path": str(quality_path),
        "report_rows": len(quality),
        "categories": categories,
        "statuses": statuses,
        "conflict_classes": conflict_classes,
        "unexplained_conflicts": unexplained_conflicts,
        "pit_excluded": pit_excluded,
        "consensus_excluded": consensus_excluded,
        "excluded_empty_artifacts": sum(
            info["excluded_empty_artifacts"] for info in summary.values()
        ),
        "recommended_unique_key": FORECAST_UNIQUE_KEY_TEXT,
        "source_verification": {
            "checked_at": "2026-08-08",
            "database": "CSMAR 分析师预测（database_id=1）",
            "tables": "294-304、7345-7346",
            "report_master_found": False,
            "conclusion": CSMAR_SOURCE_VERIFICATION,
        },
    }
