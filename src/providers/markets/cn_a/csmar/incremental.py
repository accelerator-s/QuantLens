from __future__ import annotations

import time
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

import pandas as pd
from rich.console import Console

from paths import workspace
from providers.exceptions import ProviderDataError, ProviderNetworkError
from providers.protocol import UpdateStatus

from .manifest import (
    append_download_record,
    read_manifest,
    record_audit_complete,
    record_refresh_complete,
)
from .mapper import TABLE_PRIMARY_KEYS, CSMARMapper
from .tables import CSMARTableConfig, get_table_config, require_verified

_CSMAR_DATA_AVAILABLE_TIME = dtime(17, 30)
_CN_TZ = timezone(timedelta(hours=8))

_console = Console()

_DAILY_AVG_ROWS = 5500

_DEFAULT_START_DATES: dict[str, str] = {
    "prices_daily": "2020-01-01",
    
    "returns_daily": "1990-01-01",
    "industry_classification": "1990-01-01",

    "top_shareholders": "2003-01-01",
    "top_float_shareholders": "2003-01-01",
    "inst_holdings": "1998-01-01",
    
    "analyst_forecast": "2001-01-01",
    "analyst_rating": "2001-01-01",
    "analyst_target_price": "2009-08-04",
}
_FUNDAMENTAL_DEFAULT_START = "2000-01-01"

ROLLING_OVERLAP_DAYS: dict[str, int] = {
    "fs_balance": 730,
    "fs_income": 730,
    "fs_cashflow": 730,
    "disclosure_dates": 730,
    "industry_classification": 365,
    
    "top_shareholders": 730,
    "top_float_shareholders": 730,
    "inst_holdings": 730,

    "analyst_forecast": 730,
    "analyst_rating": 730,
    "analyst_target_price": 730,
}

FAST_REFRESH_DAYS: dict[str, int] = {
    "fs_balance": 270,
    "fs_income": 270,
    "fs_cashflow": 270,
    "disclosure_dates": 270,
    "industry_classification": 90,
    "top_shareholders": 270,
    "top_float_shareholders": 270,
    "inst_holdings": 270,
    "analyst_forecast": 45,
    "analyst_rating": 45,
    "analyst_target_price": 45,
}

REFRESH_INTERVAL_DAYS: dict[str, int] = {
    "fs_balance": 7, "fs_income": 7, "fs_cashflow": 7, "disclosure_dates": 7,
    "top_shareholders": 14, "top_float_shareholders": 14, "inst_holdings": 14,
    "industry_classification": 30,
    "analyst_forecast": 7, "analyst_rating": 7, "analyst_target_price": 7,
}

DEEP_REFRESH_INTERVAL_DAYS = 90

AUDIT_INTERVAL_DAYS = 365

_PERIOD_ENDS = ("03-31", "06-30", "09-30", "12-31")

_REPORT_PERIOD_TIME_FIELDS = frozenset({"Accper", "Reptdt", "EndDate"})

_MODE_LABELS = {
    "initial": "首次全量",
    "incremental": "增量下载",
    "refresh": "快速刷新",
    "rolling": "滚动重抓",
    "audit": "全历史审计",
}

_MAX_BATCH_RETRIES = 3
_RETRY_DELAY_SECONDS = 30

@dataclass(frozen=True)
class UpdateWindow:

    table: str
    mode: str  
    start_date: str
    end_date: str
    batch_count: int

    uncovered_before: str | None = None

    @property
    def mode_label(self) -> str:
        
        return _MODE_LABELS.get(self.mode, self.mode)

@dataclass
class BatchRunStats:

    table: str
    mode: str
    start_date: str
    end_date: str
    batches_total: int = 0
    batches_ok: int = 0
    batches_empty: int = 0
    downloaded_rows: int = 0
    new_rows: int = 0
    changed_rows: int = 0
    
    excluded_empty_artifacts: int = 0
    duration_seconds: float = 0.0
    
    legal_empty_batches: list[str] = field(default_factory=list)

def _staged_path(table_name: str = "prices_daily") -> Path:
    
    return workspace().staged_for("CN-A", "stock") / f"{table_name}.parquet"

def get_last_date(table_name: str = "prices_daily") -> date | None:
    
    iso = read_manifest().last_date_for(table_name)
    return date.fromisoformat(iso) if iso else None

def _default_start_date(table_name: str) -> str:
    return _DEFAULT_START_DATES.get(table_name, _FUNDAMENTAL_DEFAULT_START)

def csmar_latest_available_date() -> date:
    
    from market_rules.markets.cn_a.calendar import ChinaAShareCalendar

    now_cn = datetime.now(_CN_TZ)
    today = now_cn.date()
    cal = ChinaAShareCalendar()

    if cal.is_trading_day(today) and now_cn.time() >= _CSMAR_DATA_AVAILABLE_TIME:
        return today

    return cal.prev_trading_day(today)

def _latest_available_date(table_name: str) -> date:
    
    if table_name in {"prices_daily", "returns_daily"}:
        return csmar_latest_available_date()
    return datetime.now(_CN_TZ).date()

def _audit_due(table_name: str, default_start: str, today: date) -> bool:
    
    last_audit = read_manifest().last_audit_date_for(table_name, default_start)
    if not last_audit:
        return True
    return (today - date.fromisoformat(last_audit)).days >= AUDIT_INTERVAL_DAYS


def refresh_schedule(table_name: str) -> dict:
    manifest = read_manifest()
    interval = REFRESH_INTERVAL_DAYS.get(table_name, 1)
    audit = manifest.last_audit_date_for(table_name, _default_start_date(table_name))
    deep = max(manifest.last_refresh_date_for(table_name, "deep"), audit)
    checked = max(manifest.last_refresh_date_for(table_name, "fast"), deep)
    deep_interval = 180 if table_name == "industry_classification" else DEEP_REFRESH_INTERVAL_DAYS
    rolling = table_name in ROLLING_OVERLAP_DAYS
    next_checks = [(date.fromisoformat(value) + timedelta(days=days)).isoformat()
                   for value, days in ((checked, interval), (deep, deep_interval), (audit, AUDIT_INTERVAL_DAYS))
                   if value] if rolling else []
    if not rolling:
        checked = max([checked] + [rec.timestamp.astimezone(_CN_TZ).date().isoformat()
                                  for rec in manifest.downloads if rec.table_name == table_name and rec.all_market])
    return {
        "table": table_name,
        "interval_days": interval,
        "overlap_days": FAST_REFRESH_DAYS.get(table_name, 0),
        "deep_interval_days": deep_interval if rolling else None,
        "audit_interval_days": AUDIT_INTERVAL_DAYS if rolling else None,
        "last_checked": checked or None,
        "next_check": min(next_checks) if next_checks else None,
        "label": f"每 {interval} 天检查" if rolling else "交易日增量",
    }

def audit_due(table_name: str) -> bool:
    
    if table_name not in ROLLING_OVERLAP_DAYS:
        return False
    today = datetime.now(_CN_TZ).date()
    return _audit_due(table_name, _default_start_date(table_name), today)

def compute_update_window(
    table_name: str,
    *,
    force_audit: bool = False,
    skip_audit: bool = False,
) -> UpdateWindow | None:
    
    cfg = get_table_config(table_name)
    default_start = _default_start_date(table_name)
    end_date = _latest_available_date(table_name).isoformat()
    today = datetime.now(_CN_TZ).date()

    def _window(mode: str, start: str, uncovered: str | None = None) -> UpdateWindow:
        batches = _batches_for(cfg, start, end_date)
        return UpdateWindow(
            table=table_name,
            mode=mode,
            start_date=start,
            end_date=end_date,
            batch_count=len(batches),
            uncovered_before=uncovered,
        )

    if force_audit:
        return _window("audit", default_start)

    manifest = read_manifest()
    last_iso = manifest.last_date_for(table_name)
    if not last_iso:
        return _window("initial", default_start)

    is_rolling = table_name in ROLLING_OVERLAP_DAYS
    if is_rolling and not skip_audit and _audit_due(table_name, default_start, today):
        return _window("audit", default_start)

    next_start = (date.fromisoformat(last_iso) + timedelta(days=1)).isoformat()

    if not is_rolling:
        if next_start > end_date:
            return None
        return _window("incremental", next_start)

    rolling_start = (today - timedelta(days=ROLLING_OVERLAP_DAYS[table_name])).isoformat()
    if next_start <= rolling_start:
        
        return _window("incremental", next_start)
    last_audit = manifest.last_audit_date_for(table_name, default_start)
    last_deep = max(manifest.last_refresh_date_for(table_name, "deep"), last_audit)
    deep_interval = 180 if table_name == "industry_classification" else DEEP_REFRESH_INTERVAL_DAYS
    if not last_deep or (today - date.fromisoformat(last_deep)).days >= deep_interval:
        return _window("rolling", rolling_start, uncovered=rolling_start)

    last_fast = max(manifest.last_refresh_date_for(table_name, "fast"), last_deep)
    if last_fast and (today - date.fromisoformat(last_fast)).days < REFRESH_INTERVAL_DAYS[table_name]:
        return None

    fast_start = (today - timedelta(days=FAST_REFRESH_DAYS[table_name])).isoformat()
    
    if date.fromisoformat(last_deep) >= today:
        return None
    return _window("refresh", fast_start, uncovered=fast_start)

def check_update_needed(
    table_name: str = "prices_daily",
) -> UpdateStatus:
    
    cfg = get_table_config(table_name)
    last = get_last_date(table_name)
    latest_available = _latest_available_date(table_name)

    if last is None:
        return UpdateStatus(
            needs_update=True,
            last_date=None,
            pending_days=0,
            estimated_rows=0,
            available_date=latest_available.isoformat(),
        )

    pending_days = (latest_available - last).days
    if pending_days <= 0:
        return UpdateStatus(
            needs_update=False,
            last_date=last.isoformat(),
            pending_days=0,
            estimated_rows=0,
            available_date=latest_available.isoformat(),
        )

    start_date = (last + timedelta(days=1)).isoformat()
    end_date = latest_available.isoformat()
    batches = _batches_for(cfg, start_date, end_date)
    estimated_rows = pending_days * _DAILY_AVG_ROWS if table_name == "prices_daily" else 0

    return UpdateStatus(
        needs_update=True,
        last_date=last.isoformat(),
        pending_days=pending_days,
        estimated_rows=estimated_rows,
        available_date=end_date,
        start_date=start_date,
        end_date=end_date,
        batch_count=len(batches),
    )

def _year_batches(start_date: str, end_date: str) -> list[tuple[str, str]]:
    
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)

    batches: list[tuple[str, str]] = []
    current = sd
    while current <= ed:
        year_end = date(current.year, 12, 31)
        batch_end = min(year_end, ed)
        batches.append((current.isoformat(), batch_end.isoformat()))
        current = batch_end + timedelta(days=1)

    return batches

def _quarter_batches(start_date: str, end_date: str) -> list[tuple[str, str]]:
    
    quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    quarter_starts = [(1, 1), (4, 1), (7, 1), (10, 1)]

    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)

    batches: list[tuple[str, str]] = []
    current = sd
    while current <= ed:
        for (sm, sd_), (em, ed_) in zip(quarter_starts, quarter_ends, strict=True):
            q_start = date(current.year, sm, sd_)
            q_end = date(current.year, em, ed_)
            if q_end < current:
                continue
            batch_start = max(current, q_start)
            batch_end = min(q_end, ed)
            if batch_start <= batch_end:
                batches.append((batch_start.isoformat(), batch_end.isoformat()))
            current = q_end + timedelta(days=1)
            break
        else:
            break

    return batches

def _batches_for(
    cfg: CSMARTableConfig,
    start_date: str,
    end_date: str,
    batch_freq: str | None = None,
) -> list[tuple[str, str]]:
    
    freq = batch_freq or cfg.batch_freq
    if freq == "quarter":
        return _quarter_batches(start_date, end_date)
    return _year_batches(start_date, end_date)

def _period_ends_in_range(start_date: str, end_date: str) -> list[date]:
    
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)
    ends: list[date] = []
    for year in range(sd.year, ed.year + 1):
        for mmdd in _PERIOD_ENDS:
            month, day = (int(p) for p in mmdd.split("-"))
            pe = date(year, month, day)
            if sd <= pe <= ed:
                ends.append(pe)
    return ends

def _range_has_valid_period_end(start_date: str, end_date: str) -> bool:
    
    return bool(_period_ends_in_range(start_date, end_date))

def _any_deadline_passed(period_ends: list[date]) -> bool:
    
    from .fundamentals import disclosure_deadline_maps

    deadline_map, offset_map = disclosure_deadline_maps()
    today = datetime.now(_CN_TZ).date()
    for pe in period_ends:
        mmdd = f"{pe.month:02d}-{pe.day:02d}"
        deadline_mmdd = deadline_map.get(mmdd)
        if deadline_mmdd is None:
            
            return True
        month, day = (int(p) for p in deadline_mmdd.split("-"))
        deadline = date(pe.year + offset_map.get(mmdd, 0), month, day)
        if deadline < today:
            return True
    return False

_prices_dates_cache: tuple[tuple[int, int], tuple[str, ...]] | None = None

def _prices_daily_dates() -> tuple[str, ...]:
    
    global _prices_dates_cache
    path = _staged_path("prices_daily")
    if not path.exists():
        return ()
    stat = path.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    if _prices_dates_cache is not None and _prices_dates_cache[0] == key:
        return _prices_dates_cache[1]
    dates = pd.read_parquet(path, columns=["date"])["date"]
    result = tuple(sorted(dates.unique()))
    _prices_dates_cache = (key, result)
    return result

def _prices_have_dates_in_range(start_date: str, end_date: str) -> bool:
    
    dates = _prices_daily_dates()
    if not dates:
        return False
    i = bisect_left(dates, start_date)
    return i < len(dates) and dates[i] <= end_date

def _classify_empty_batch(
    staged_name: str,
    cfg: CSMARTableConfig,
    batch_start: str,
    batch_end: str,
) -> tuple[bool, str]:
    
    if staged_name == "prices_daily":
        return False, "行情批次必然包含交易日"
    if staged_name == "returns_daily":

        if _prices_have_dates_in_range(batch_start, batch_end):
            return False, "区间内 prices_daily 已有交易日，回报数据不应为空"
        return True, "区间内 prices_daily 无交易日（早于市场首个交易日或非交易区间）"
    if cfg.time_set_field in _REPORT_PERIOD_TIME_FIELDS:
        period_ends = _period_ends_in_range(batch_start, batch_end)
        if not period_ends:
            return True, "区间内无报告期期末"
        if _any_deadline_passed(period_ends):
            return False, "区间内报告期的法定披露截止日已过，应有已披露数据"
        return True, "区间内报告期的法定披露截止日尚未到达"
    if staged_name == "industry_classification":
        return True, "该年度无新生效的行业分类记录（0 行）"
    if staged_name == "analyst_target_price":

        return True, "CSMAR 目标价表在该区间明确返回 0 条"
    return False, "该表未定义合法空批次规则"

def download_table_full(
    staged_name: str,
    start_date: str,
    end_date: str,
    *,
    instrument_codes: list[str] | None = None,
    on_progress: Callable[[dict], None] | None = None,
    shared_daily: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    
    from .client import CSMARClient

    cfg = get_table_config(staged_name)
    require_verified(cfg)

    mapper = CSMARMapper()
    codes = instrument_codes if instrument_codes is not None else []
    all_market = instrument_codes is None

    share = shared_daily is not None and staged_name in {'prices_daily', 'returns_daily'}
    key = (start_date, end_date, tuple(codes), all_market)
    cached = shared_daily.pop(key, None) if share else None
    fields = cfg.to_client_fields()
    if share:
        fields = list({f['id']: f for name in ('prices_daily', 'returns_daily')
                       for f in get_table_config(name).to_client_fields()}.values())
    if cached is not None:
        raw_df = cached
        duration = 0.0
        if on_progress:
            on_progress({'state': 'parsing', 'pack_stage': 'reused', 'pack_percent': 100})
    else:
        with CSMARClient(on_progress=on_progress) as client:
            client.login()
            t0 = time.time()
            raw_df = client.download(
                stock_codes=codes,
                start_date=start_date,
                end_date=end_date,
                table_name=cfg.table_name,
                table_name_phy=cfg.table_name_phy,
                table_id=cfg.table_id,
                fields=fields,
                code_set_field=cfg.code_set_field,
                time_set_field=cfg.time_set_field,
            )
            duration = time.time() - t0
        if share:
            shared_daily[key] = raw_df
    if share:
        wanted = {f.field for f in cfg.fields}
        raw_df = raw_df.loc[:, [c for c in raw_df.columns if c in wanted]]

    df = mapper.to_standard(raw_df, staged_name)
    excluded_empty_artifacts = int(df.attrs.get("excluded_empty_export_artifacts", 0))

    errors = mapper.validate_schema(df, staged_name)
    if errors:
        raise ProviderDataError(
            code="PROVIDER_CONTRACT_VALIDATION_FAILED",
            message=f"数据契约校验失败：{'; '.join(errors)}",
            context={"errors": errors, "staged_name": staged_name},
            blocking=True,
        )

    return df, {
        "table_name": staged_name,
        "start_date": start_date,
        "end_date": end_date,
        "stock_codes": codes,
        "all_market": all_market,
        "records_count": len(df),
        "duration_seconds": duration,
        "excluded_empty_artifacts": excluded_empty_artifacts,
    }

def download_full(
    start_date: str,
    end_date: str,
    *,
    instrument_codes: list[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    
    return download_table_full(
        "prices_daily",
        start_date,
        end_date,
        instrument_codes=instrument_codes,
    )

def _record_legal_empty(
    stats: BatchRunStats,
    staged_name: str,
    batch_start: str,
    batch_end: str,
    instrument_codes: list[str] | None,
    reason: str,
    console: Console,
) -> None:
    
    console.print(f"  [yellow]合法空批次：{reason}。[/yellow]")
    append_download_record(
        table_name=staged_name,
        start_date=batch_start,
        end_date=batch_end,
        stock_codes=instrument_codes or [],
        all_market=instrument_codes is None,
        records_count=0,
        mode="legal_empty",
    )
    stats.batches_empty += 1
    stats.legal_empty_batches.append(f"{batch_start}~{batch_end}（{reason}）")

def _download_batch_with_retry(
    staged_name: str,
    cfg: CSMARTableConfig,
    batch_start: str,
    batch_end: str,
    *,
    instrument_codes: list[str] | None,
    batch_index: int,
    batch_total: int,
    remaining_batches: list[str],
    console: Console,
    on_progress: Callable[[dict], None] | None = None,
    shared_daily: dict | None = None,
) -> tuple[pd.DataFrame | None, dict | None, str | None]:
    
    last_error: ProviderDataError | ProviderNetworkError | None = None
    for attempt in range(1, _MAX_BATCH_RETRIES + 1):
        try:
            with console.status("正在从 CSMAR 下载数据…"):
                df, record_info = download_table_full(
                    staged_name,
                    batch_start,
                    batch_end,
                    instrument_codes=instrument_codes,
                    on_progress=on_progress,
                    shared_daily=shared_daily,
                )
            return df, record_info, None
        except (ProviderDataError, ProviderNetworkError) as e:

            if e.code in {"PROVIDER_PHONE_VERIFICATION_REQUIRED","PROVIDER_PACK_STUCK","PROVIDER_PACK_TIMEOUT",
                          "PROVIDER_PACK_CONNECTION_LOST", "PROVIDER_PACK_RESPONSE_INVALID", "PROVIDER_PACK_ERROR"}:
                raise
            if e.code == "PROVIDER_EMPTY_RESULT":
                legal, reason = _classify_empty_batch(staged_name, cfg, batch_start, batch_end)
                if legal:
                    return None, None, reason
                raise ProviderDataError(
                    code="PROVIDER_EMPTY_RESULT_UNEXPECTED",
                    message=(
                        f"表 {staged_name} 批次 {batch_start}~{batch_end} 返回 0 行，"
                        f"但{reason}，更新已中止。"
                    ),
                    context={
                        "table": staged_name,
                        "batch_start": batch_start,
                        "batch_end": batch_end,
                        "batch_index": batch_index,
                        "batch_total": batch_total,
                    },
                    blocking=True,
                    related_batches=[f"{staged_name}:{batch_start}~{batch_end}"],
                    suggested_action=(
                        "空结果不符合该表数据预期，可能为数据源缺数或查询参数异常。"
                        "已成功批次不回滚；请登录 data.csmar.com 人工核对该区间数据，"
                        "确认后重试或创建 data_quality_conflict issue。"
                    ),
                ) from e
            last_error = e
            if attempt < _MAX_BATCH_RETRIES:
                console.print(
                    f"  [yellow]第 {attempt} 次尝试失败（{e.code}），"
                    f"{_RETRY_DELAY_SECONDS}s 后重试…[/yellow]"
                )
                time.sleep(_RETRY_DELAY_SECONDS)

    raise ProviderDataError(
        code="PROVIDER_BATCH_RETRY_EXHAUSTED",
        message=(
            f"表 {staged_name} 批次 {batch_start}~{batch_end}"
            f"（第 {batch_index}/{batch_total} 批）连续 {_MAX_BATCH_RETRIES} 次下载失败，"
            "更新已中止，后续批次未执行。"
        ),
        context={
            "table": staged_name,
            "batch_start": batch_start,
            "batch_end": batch_end,
            "batch_index": batch_index,
            "batch_total": batch_total,
            "last_error_code": getattr(last_error, "code", ""),
        },
        blocking=True,
        related_batches=remaining_batches,
        suggested_action=(
            "已成功批次不回滚，manifest 水位停在失败批次之前；"
            "请检查校园网连接与 CSMAR 服务状态后重新执行 data update，"
            "将自动从失败区间继续。若持续失败请创建 bug issue。"
        ),
    ) from last_error

def download_table_batched(
    staged_name: str,
    start_date: str,
    end_date: str,
    *,
    instrument_codes: list[str] | None = None,
    label: str = "批量下载",
    batch_freq: str | None = None,
    mode: str = "",
    console: Console | None = None,
    on_progress: Callable[[dict], None] | None = None,
    shared_daily: dict | None = None,
) -> BatchRunStats:
    
    cfg = get_table_config(staged_name)
    require_verified(cfg)
    out = console or _console

    batches = _batches_for(cfg, start_date, end_date, batch_freq)
    stats = BatchRunStats(
        table=staged_name,
        mode=mode,
        start_date=start_date,
        end_date=end_date,
        batches_total=len(batches),
    )
    if not batches:
        return stats

    scope = "全市场" if instrument_codes is None else f"{len(instrument_codes)} 只标的"
    freq_label = "季度" if (batch_freq or cfg.batch_freq) == "quarter" else "年度"

    out.print(
        f"[bold]{label}[/bold]（{staged_name}）：{scope}，"
        f"{start_date} → {end_date}，共 {len(batches)} 个{freq_label}批次"
    )

    t_run = time.time()

    for i, (batch_start, batch_end) in enumerate(batches, 1):
        out.print(f"\n[cyan]批次 {i}/{len(batches)}[/cyan]：{batch_start} → {batch_end}")
        if on_progress is not None:
            on_progress(
                {
                    "state": "downloading",
                    "pack_percent": None,
                    "pack_stage": "waiting",
                    "pack_elapsed_seconds": 0,
                    "pack_estimate_minutes": None,
                    "table": staged_name,
                    "batch_index": i,
                    "batch_total": len(batches),
                    "window": f"{batch_start} → {batch_end}",
                }
            )

        if cfg.time_set_field in _REPORT_PERIOD_TIME_FIELDS and not _range_has_valid_period_end(
            batch_start, batch_end
        ):
            _record_legal_empty(
                stats,
                staged_name,
                batch_start,
                batch_end,
                instrument_codes,
                "区间内无报告期期末，预跳过",
                out,
            )
            if on_progress is not None:
                on_progress({"state": "batch_completed", "batch_index": i})
            continue

        last_pack = None
        def pack_progress(event):
            nonlocal last_pack
            current = (event.get("pack_stage"), event.get("pack_percent"))
            if current != last_pack:
                last_pack = current
                stage = event.get('pack_stage')
                if stage == 'queued':
                    out.print('  CSMAR 排队中')
                elif stage == 'transferring':
                    out.print('  正在下载压缩包')
                elif stage == 'parsing':
                    out.print('  正在解析数据')
                elif stage == 'reused':
                    out.print('  复用本次已下载的日行情数据')
                elif event.get("pack_percent") is not None:
                    out.print(f"  CSMAR 打包 {event['pack_percent']:g}% · {event['pack_elapsed_seconds']} 秒")
            if on_progress:
                on_progress({**event, "batch_index": i, "batch_total": len(batches),
                             "window": f"{batch_start} → {batch_end}"})

        df, record_info, legal_empty_reason = _download_batch_with_retry(
            staged_name,
            cfg,
            batch_start,
            batch_end,
            instrument_codes=instrument_codes,
            batch_index=i,
            batch_total=len(batches),
            remaining_batches=[f"{staged_name}:{bs}~{be}" for bs, be in batches[i - 1 :]],
            console=out,
            on_progress=pack_progress,
            shared_daily=shared_daily,
        )

        if legal_empty_reason is not None:
            _record_legal_empty(
                stats,
                staged_name,
                batch_start,
                batch_end,
                instrument_codes,
                legal_empty_reason,
                out,
            )
            if on_progress is not None:
                on_progress({"state": "batch_completed", "batch_index": i})
            continue

        assert df is not None and record_info is not None  # 非合法空时必有数据

        excluded_empty_artifacts = int(record_info.pop("excluded_empty_artifacts", 0))
        stats.excluded_empty_artifacts += excluded_empty_artifacts
        if excluded_empty_artifacts:
            out.print(
                f"  [yellow]排除业务字段全空的导出脚注残片 {excluded_empty_artifacts} 条。[/yellow]"
            )

        if df.empty:
            
            _record_legal_empty(
                stats,
                staged_name,
                batch_start,
                batch_end,
                instrument_codes,
                "映射过滤后 0 行（如仅含期初数行）",
                out,
            )
            if on_progress is not None:
                on_progress({"state": "batch_completed", "batch_index": i})
            continue

        out.print(f"  下载完成：{len(df)} 条记录")

        new_count, changed_count = _merge_to_staged(df, staged_name)
        stats.downloaded_rows += len(df)
        stats.new_rows += new_count
        stats.changed_rows += changed_count
        stats.batches_ok += 1
        out.print(f"  写入 staged：新增 {new_count} 条，修订 {changed_count - new_count} 条，其余复用")

        record_info["mode"] = mode
        append_download_record(**record_info)

        if staged_name == "prices_daily" and "instrument_id" in df.columns:
            instruments_df = (
                df[["instrument_id"]]
                .drop_duplicates()
                .assign(source="csmar")
                .reset_index(drop=True)
            )
            inst_new = append_to_staged(instruments_df, "instruments")
            if inst_new > 0:
                out.print(f"  instruments 新增 {inst_new} 条")
        if on_progress is not None:
            on_progress(
                {
                    "state": "batch_completed",
                    "batch_index": i,
                    "downloaded_rows": stats.downloaded_rows,
                    "new_rows": stats.new_rows,
                }
            )

    stats.duration_seconds = time.time() - t_run

    out.print(
        f"\n[green bold]{label}完成[/green bold]（{staged_name}）："
        f"下载 {stats.downloaded_rows} 条，净新增 {stats.new_rows} 条，"
        f"排除空导出残片 {stats.excluded_empty_artifacts} 条，"
        f"批次 成功 {stats.batches_ok} / 合法空 {stats.batches_empty} / 共 {stats.batches_total}"
    )
    return stats

def download_full_batched(
    start_date: str,
    end_date: str,
    *,
    instrument_codes: list[str] | None = None,
) -> BatchRunStats:
    
    return download_table_batched(
        "prices_daily",
        start_date,
        end_date,
        instrument_codes=instrument_codes,
        batch_freq="year",
    )

def run_update_window(
    window: UpdateWindow,
    *,
    instrument_codes: list[str] | None = None,
    console: Console | None = None,
    on_progress: Callable[[dict], None] | None = None,
    shared_daily: dict | None = None,
) -> BatchRunStats:
    
    stats = download_table_batched(
        window.table,
        window.start_date,
        window.end_date,
        instrument_codes=instrument_codes,
        label=window.mode_label,
        mode=window.mode,
        console=console,
        on_progress=on_progress,
        shared_daily=shared_daily,
    )
    complete = stats.batches_total > 0 and stats.batches_ok + stats.batches_empty == stats.batches_total
    if complete and instrument_codes is None:
        checked = datetime.now(_CN_TZ).date().isoformat()
        record_refresh_complete(window.table, "fast", checked)
        if window.mode in ("audit", "initial", "rolling"):
            record_refresh_complete(window.table, "deep", checked)
        if window.mode in ("audit", "initial"):
            record_audit_complete(window.table, checked)
    return stats

def download_incremental(
    *,
    instrument_codes: list[str] | None = None,
    table_name: str = "prices_daily",
    force_audit: bool = False,
    skip_audit: bool = False,
) -> BatchRunStats | None:
    
    window = compute_update_window(table_name, force_audit=force_audit, skip_audit=skip_audit)
    if window is None:
        return None
    return run_update_window(window, instrument_codes=instrument_codes)

def append_to_staged(
    df: pd.DataFrame,
    table_name: str = "prices_daily",
) -> int:
    return _merge_to_staged(df, table_name)[0]


def _merge_to_staged(df: pd.DataFrame, table_name: str) -> tuple[int, int]:
    if df.empty:
        return 0, 0

    path = _staged_path(table_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    pk = TABLE_PRIMARY_KEYS.get(table_name, ["instrument_id"])
    incoming = df.drop_duplicates(subset=pk, keep="last").set_index(pk)
    if path.exists():
        existing = pd.read_parquet(path).drop_duplicates(subset=pk, keep="last").set_index(pk)
        # A fresh retrieval timestamp alone is not a revision of a business row.
        compared = incoming.drop(columns=['downloaded_at'], errors='ignore')
        previous = existing.reindex(index=incoming.index, columns=compared.columns)
        same = (previous.eq(compared) | (previous.isna() & compared.isna())).all(axis=1)
        if not incoming.columns.isin(existing.columns).all():
            same[:] = False
        added = ~incoming.index.isin(existing.index)
        changed = incoming.loc[~same | added]
        if changed.empty:
            return 0, 0
        # Preserve enrichment columns; their owner recomputes them after source changes.
        updates = existing.reindex(changed.index).copy()
        for column in incoming.columns:
            updates[column] = changed[column]
        combined = pd.concat([existing.loc[~existing.index.isin(changed.index)], updates])
        new_count = int(added.sum())
    else:
        combined = incoming
        changed = incoming
        new_count = len(incoming)
    combined = combined.reset_index().sort_values(pk).reset_index(drop=True)
    combined.to_parquet(path, index=False)
    return new_count, len(changed)

def save_raw(raw_bytes: bytes, filename: str) -> Path:
    
    raw_dir = workspace().raw_for("CN-A", "stock", "csmar")
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / filename
    path.write_bytes(raw_bytes)
    return path
