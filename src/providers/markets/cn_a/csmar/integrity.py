from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .manifest import CSMARManifest, read_manifest
from .mapper import TABLE_PRIMARY_KEYS
from .tables import TABLE_REGISTRY

@dataclass
class TableIntegrity:

    table: str
    exists: bool = False
    rows: int = 0
    pk_duplicates: int = 0
    watermark: str = ""
    coverage_gaps: list[str] = field(default_factory=list)

@dataclass
class AnnQualitySummary:

    exists: bool = False
    rows: int = 0
    unique_pairs: int = 0
    categories: dict[str, int] = field(default_factory=dict)

@dataclass
class AnalystQualitySummary:

    exists: bool = False
    rows: int = 0
    unique_pairs: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, int] = field(default_factory=dict)
    conflict_classes: dict[str, int] = field(default_factory=dict)
    unexplained_conflicts: int = 0
    pit_excluded: int = 0

@dataclass
class IntegrityReport:

    tables: list[TableIntegrity] = field(default_factory=list)
    ann_quality: AnnQualitySummary = field(default_factory=AnnQualitySummary)
    analyst_quality: AnalystQualitySummary = field(default_factory=AnalystQualitySummary)

    @property
    def has_errors(self) -> bool:
        
        return any(t.pk_duplicates > 0 or t.coverage_gaps for t in self.tables) or bool(
            self.analyst_quality.unexplained_conflicts
        )

def _next_day(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()

def _prev_day(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()

def _coverage_gaps(manifest: CSMARManifest, table_name: str) -> list[str]:
    
    intervals = sorted(
        (rec.start_date, rec.end_date)
        for rec in manifest.downloads
        if (
            rec.table_name == table_name
            and rec.all_market
            and rec.start_date
            and rec.end_date
            and rec.start_date <= rec.end_date
        )
    )
    if not intervals:
        return []

    gaps: list[str] = []
    covered_end = intervals[0][1]
    for start, end in intervals[1:]:
        if start > _next_day(covered_end):
            gaps.append(f"{_next_day(covered_end)}~{_prev_day(start)}")
        if end > covered_end:
            covered_end = end
    return gaps

def _check_table(manifest: CSMARManifest, table_name: str) -> TableIntegrity:
    
    import pandas as pd

    from .incremental import _staged_path

    result = TableIntegrity(
        table=table_name,
        watermark=manifest.last_date_for(table_name),
        coverage_gaps=_coverage_gaps(manifest, table_name),
    )

    path = _staged_path(table_name)
    if not path.exists():
        return result

    pk = TABLE_PRIMARY_KEYS.get(table_name, ["instrument_id"])
    df = pd.read_parquet(path, columns=pk)
    result.exists = True
    result.rows = len(df)
    result.pk_duplicates = int(df.duplicated(subset=pk).sum())
    return result

def _check_ann_quality() -> AnnQualitySummary:
    
    import pandas as pd

    from .fundamentals import quality_report_path

    path = quality_report_path()
    if not path.exists():
        return AnnQualitySummary()

    report = pd.read_parquet(path, columns=["instrument_id", "report_period", "gap_category"])
    pairs = report.drop_duplicates(subset=["instrument_id", "report_period"])
    return AnnQualitySummary(
        exists=True,
        rows=len(report),
        unique_pairs=len(pairs),
        categories=pairs["gap_category"].value_counts().to_dict() if len(pairs) else {},
    )

def _check_analyst_quality() -> AnalystQualitySummary:
    
    import pandas as pd

    from .analyst import analyst_quality_report_path

    path = analyst_quality_report_path()
    if not path.exists():
        return AnalystQualitySummary()

    report = pd.read_parquet(path)
    records = report.drop_duplicates(subset=["table", "instrument_id", "report_id"])
    categories = report["gap_category"].value_counts().to_dict() if len(report) else {}
    if "quality_status" in report.columns:
        statuses = report["quality_status"].value_counts().to_dict()
        unexplained = int(report["quality_status"].eq("unexplained_conflict").sum())
    else:
        
        statuses = {}
        unexplained = int(categories.get("business_key_duplicate", 0))
    conflict_classes = (
        report["conflict_class"].value_counts().to_dict()
        if "conflict_class" in report.columns
        else {}
    )
    pit_excluded = (
        int(report["pit_eligible"].eq(False).sum()) if "pit_eligible" in report.columns else 0
    )
    return AnalystQualitySummary(
        exists=True,
        rows=len(report),
        unique_pairs=len(records),
        categories=categories,
        statuses=statuses,
        conflict_classes=conflict_classes,
        unexplained_conflicts=unexplained,
        pit_excluded=pit_excluded,
    )

def run_integrity_checks() -> IntegrityReport:
    
    manifest = read_manifest()
    return IntegrityReport(
        tables=[_check_table(manifest, name) for name in TABLE_REGISTRY],
        ann_quality=_check_ann_quality(),
        analyst_quality=_check_analyst_quality(),
    )
