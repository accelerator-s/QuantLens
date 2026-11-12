from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Console

from exceptions import QuantTrainingError

from .incremental import (
    ROLLING_OVERLAP_DAYS,
    BatchRunStats,
    UpdateWindow,
    _default_start_date,
    audit_due,
    compute_update_window,
    run_update_window,
    refresh_schedule,
)
from .integrity import IntegrityReport, run_integrity_checks

UPDATE_ORDER: tuple[str, ...] = (
    "prices_daily",
    "returns_daily",
    "fs_balance",
    "fs_income",
    "fs_cashflow",
    "disclosure_dates",
    "industry_classification",
    "top_shareholders",
    "top_float_shareholders",
    "inst_holdings",
    "analyst_forecast",
    "analyst_rating",
    "analyst_target_price",
)

_ANN_DATE_TABLES = frozenset({"fs_balance", "fs_income", "fs_cashflow", "disclosure_dates"})

_HOLDER_ENRICH_TABLES = frozenset(
    {"top_shareholders", "top_float_shareholders", "inst_holdings", "disclosure_dates"}
)

_ANALYST_ENRICH_TABLES = frozenset({"analyst_forecast", "analyst_rating", "analyst_target_price"})

_console = Console()

@dataclass
class TableOutcome:

    table: str
    window: UpdateWindow | None
    stats: BatchRunStats | None

@dataclass
class AutoUpdatePlan:

    windows: list[tuple[str, UpdateWindow | None]]
    
    deferred_audits: list[str] = field(default_factory=list)

    skipped_unverified: list[str] = field(default_factory=list)
    schedules: list[dict] = field(default_factory=list)

    @property
    def has_pending(self) -> bool:
        
        return any(window is not None for _, window in self.windows)

@dataclass
class AutoUpdateReport:

    outcomes: list[TableOutcome] = field(default_factory=list)
    
    ann_result: dict | None = None
    
    holders_result: dict | None = None
    
    analyst_result: dict | None = None
    integrity: IntegrityReport | None = None
    
    audit_tables: list[str] = field(default_factory=list)
    
    deferred_audits: list[str] = field(default_factory=list)
    
    audit_status: dict[str, str] = field(default_factory=dict)
    
    skipped_unverified: list[str] = field(default_factory=list)

def plan_auto_update(
    *,
    force_audit: bool = False,
    skip_audit: bool = False,
) -> AutoUpdatePlan:
    
    from .tables import get_table_config

    windows: list[tuple[str, UpdateWindow | None]] = []
    deferred: list[str] = []
    skipped: list[str] = []

    for table in UPDATE_ORDER:
        if not get_table_config(table).verified:
            skipped.append(table)
            continue
        window = compute_update_window(table, force_audit=force_audit, skip_audit=skip_audit)
        windows.append((table, window))
        if skip_audit and (window is None or window.mode != "audit") and audit_due(table):
            deferred.append(table)

    return AutoUpdatePlan(windows=windows, deferred_audits=deferred, skipped_unverified=skipped,
                          schedules=[refresh_schedule(table) for table, _ in windows])


def _enrichment_due(tables, report_name):
    from paths import workspace
    staged = workspace().staged_for("CN-A", "stock")
    sources = [staged / f"{table}.parquet" for table in tables]
    existing = [path for path in sources if path.exists()]
    if not existing:
        return False
    report = staged / report_name
    return not report.exists() or any(path.stat().st_mtime_ns > report.stat().st_mtime_ns for path in existing)

def run_auto_update(
    *,
    force_audit: bool = False,
    skip_audit: bool = False,
    console: Console | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> AutoUpdateReport:
    
    plan = plan_auto_update(force_audit=force_audit, skip_audit=skip_audit)
    out = console or _console
    report = AutoUpdateReport(
        deferred_audits=plan.deferred_audits,
        skipped_unverified=plan.skipped_unverified,
    )
    need_ann = False
    need_holders = False
    need_analyst = False
    
    total_units = sum(w.batch_count for _, w in plan.windows if w is not None) + 4
    completed_units = 0
    pending_total = sum(window is not None for _, window in plan.windows)
    pending_index = 0
    daily = [window for table, window in plan.windows
             if table in {'prices_daily', 'returns_daily'} and window is not None]
    # Share only a matching single batch, keeping the raw frame bounded in memory.
    shared_daily = {} if len(daily) == 2 and all(w.batch_count == 1 for w in daily) and (
        daily[0].start_date, daily[0].end_date
    ) == (daily[1].start_date, daily[1].end_date) else None

    def emit(**payload) -> None:
        if on_progress is None:
            return
        payload.setdefault("fraction", completed_units / max(total_units, 1))
        payload.setdefault("percent", round(payload["fraction"] * 100, 1))
        if payload.get("state") in {"enriching", "checking", "phase_completed", "table_started", "table_completed", "batch_completed"}:
            payload.setdefault("pack_stage", "")
            payload.setdefault("pack_percent", None)
        on_progress(payload)

    for table in plan.skipped_unverified:
        out.print(
            f"[yellow]跳过 {table}：字段 id 未核对（tables.py 中 field_id=None），"
            "核对补全后 data update 将自动纳入。[/yellow]"
        )

    for table, window in plan.windows:
        if window is None:
            out.print(f"[green]{table} 按计划跳过。[/green]")
            report.outcomes.append(TableOutcome(table, None, None))
            continue
        pending_index += 1
        i = pending_index
        out.rule(f"[bold]{i}/{pending_total} {table}[/bold]")
        emit(
            state="table_started",
            table=table,
            table_index=i,
            table_total=pending_total,
            batch_index=0,
            batch_total=window.batch_count if window is not None else 0,
            mode=window.mode_label if window is not None else "按计划跳过",
            phase="",
            window="",
        )

        def batch_progress(event: dict) -> None:
            finished = int(event.get("batch_index", 0))
            if event.get("state") != "batch_completed":
                finished = max(finished - 1, 0)

            # 形成重复关键字参数。
            progress = {
                **event,
                "table": table,
                "table_index": i,
                "table_total": pending_total,
                "mode": window.mode_label,
                "fraction": (completed_units + finished) / max(total_units, 1),
            }
            emit(**progress)

        try:
            stats = run_update_window(window, console=out, on_progress=batch_progress,
                                      shared_daily=shared_daily)
        except QuantTrainingError as exc:
            
            exc.context.setdefault(
                "completed_tables", [o.table for o in report.outcomes if o.stats is not None]
            )
            exc.context.setdefault("failed_table", table)
            raise
        report.outcomes.append(TableOutcome(table, window, stats))
        completed_units += window.batch_count
        emit(
            state="table_completed",
            table=table,
            table_index=i,
            table_total=pending_total,
            batch_index=window.batch_count,
            batch_total=window.batch_count,
            downloaded_rows=stats.downloaded_rows,
            new_rows=stats.new_rows,
        )

        if window.mode in ("audit", "initial"):
            report.audit_tables.append(table)

        if table in _ANN_DATE_TABLES and stats.changed_rows > 0:
            need_ann = True
        if table in _HOLDER_ENRICH_TABLES and stats.changed_rows > 0:
            need_holders = True
        if table in _ANALYST_ENRICH_TABLES and stats.changed_rows > 0:
            need_analyst = True

    # Retry unfinished enrichment even when a previous download already wrote the source.
    need_ann |= _enrichment_due(_ANN_DATE_TABLES, "quality_missing_ann_date.parquet")
    need_holders |= _enrichment_due(_HOLDER_ENRICH_TABLES, "quality_holders_missing_ann_date.parquet")
    need_analyst |= _enrichment_due(_ANALYST_ENRICH_TABLES, "quality_analyst_data.parquet")

    if need_ann:
        out.rule("[bold]公告日关联[/bold]")
        out.print("检测到财报/披露表有下载数据，自动重跑公告日关联与质量报告…")
        emit(state="enriching", phase="公告日关联", table="")
        from .fundamentals import attach_ann_dates

        report.ann_result = attach_ann_dates()
    completed_units += 1
    emit(state="phase_completed", phase="公告日关联", table="")

    if need_holders:
        out.rule("[bold]机构持仓富集[/bold]")
        out.print("检测到机构持仓/披露表有下载数据，自动重跑披露日关联与股东类型分类…")
        emit(state="enriching", phase="机构持仓富集", table="")
        from .holders import attach_holder_data

        report.holders_result = attach_holder_data()
    completed_units += 1
    emit(state="phase_completed", phase="机构持仓富集", table="")

    if need_analyst:
        out.rule("[bold]分析师数据补齐[/bold]")
        out.print("检测到分析师明细有下载数据，自动重跑 ReportID 关联与质量报告…")
        emit(state="enriching", phase="分析师数据补齐", table="")
        from .analyst import attach_analyst_data

        report.analyst_result = attach_analyst_data()
    completed_units += 1
    emit(state="phase_completed", phase="分析师数据补齐", table="")

    out.rule("[bold]完整性检查[/bold]")
    emit(state="checking", phase="完整性检查", table="")
    if plan.has_pending or need_ann or need_holders or need_analyst:
        report.integrity = run_integrity_checks()
    else:
        out.print("本次按计划跳过下载，完整性扫描一并跳过。")
    completed_units += 1
    emit(state="phase_completed", phase="完整性检查", table="", fraction=1.0)

    from .manifest import read_manifest

    manifest = read_manifest()
    report.audit_status = {
        table: manifest.last_audit_date_for(table, _default_start_date(table))
        for table in UPDATE_ORDER
        if table in ROLLING_OVERLAP_DAYS
    }

    return report
