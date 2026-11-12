from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from paths import workspace

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

class DownloadRecord(BaseModel):

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=_utcnow)
    table_name: str = "prices_daily"
    start_date: str = ""
    end_date: str = ""
    stock_codes: list[str] = Field(default_factory=list)
    all_market: bool = False
    records_count: int = 0
    file_path: str = ""
    duration_seconds: float = 0.0
    
    mode: str = ""

class CSMARManifest(BaseModel):

    model_config = ConfigDict(extra="forbid")

    provider: str = "csmar"
    version: int = 1
    last_updated: datetime = Field(default_factory=_utcnow)
    
    last_daily_date: str = ""
    total_records: int = 0

    last_dates: dict[str, str] = Field(default_factory=dict)
    
    total_records_by_table: dict[str, int] = Field(default_factory=dict)
    
    last_audit_dates: dict[str, str] = Field(default_factory=dict)

    last_refresh_dates: dict[str, str] = Field(default_factory=dict)
    downloads: list[DownloadRecord] = Field(default_factory=list)

    def last_date_for(self, table_name: str) -> str:
        
        iso = self.last_dates.get(table_name, "")
        if not iso and table_name == "prices_daily":
            return self.last_daily_date
        return iso

    def last_audit_date_for(self, table_name: str, default_start: str) -> str:
        
        iso = self.last_audit_dates.get(table_name, "")
        if iso:
            return iso
        baseline_dates = [
            rec.timestamp.date().isoformat()
            for rec in self.downloads
            if (
                rec.table_name == table_name
                and rec.all_market
                and rec.start_date
                and rec.start_date <= default_start
            )
        ]
        return max(baseline_dates) if baseline_dates else ""

    def last_refresh_date_for(self, table_name: str, tier: str) -> str:
        
        value = self.last_refresh_dates.get(f"{table_name}:{tier}", "")
        if value:
            return value

        if tier != "deep":
            return ""
        from datetime import date, timedelta
        groups = {}
        for rec in self.downloads:
            if rec.table_name == table_name and rec.mode == 'rolling' and rec.all_market:
                groups.setdefault(rec.timestamp.date(), []).append((rec.start_date, rec.end_date))
        candidates = []
        for checked, ranges in groups.items():
            cursor = checked - timedelta(days=365 if table_name == 'industry_classification' else 730)
            for start, end in sorted(set(ranges)):
                if not start or not end:
                    continue
                if date.fromisoformat(start) > cursor:
                    break
                cursor = max(cursor, date.fromisoformat(end) + timedelta(days=1))
            if cursor > checked:
                candidates.append(checked.isoformat())
        return max(candidates) if candidates else ""

def _manifest_dir() -> Path:
    return workspace().manifest_for("CN-A", "stock", "csmar")

def _manifest_path() -> Path:
    return _manifest_dir() / "manifest.yaml"

_manifest_raw_cache: tuple[tuple[int, int], dict] | None = None

def read_manifest() -> CSMARManifest:
    
    global _manifest_raw_cache
    path = _manifest_path()
    if not path.exists():
        return CSMARManifest()
    stat = path.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    if _manifest_raw_cache is None or _manifest_raw_cache[0] != key:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _manifest_raw_cache = (key, raw)
    return CSMARManifest.model_validate(_manifest_raw_cache[1])

def write_manifest(manifest: CSMARManifest) -> None:
    
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest.last_updated = _utcnow()
    path.write_text(
        yaml.safe_dump(manifest.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

def append_download_record(
    *,
    table_name: str = "prices_daily",
    start_date: str,
    end_date: str,
    stock_codes: list[str],
    all_market: bool = False,
    records_count: int,
    file_path: str = "",
    duration_seconds: float = 0.0,
    mode: str = "",
) -> CSMARManifest:
    
    manifest = read_manifest()

    record = DownloadRecord(
        table_name=table_name,
        start_date=start_date,
        end_date=end_date,
        stock_codes=stock_codes,
        all_market=all_market,
        records_count=records_count,
        file_path=file_path,
        duration_seconds=duration_seconds,
        mode=mode,
    )
    manifest.downloads.append(record)
    manifest.total_records += records_count
    manifest.total_records_by_table[table_name] = (
        manifest.total_records_by_table.get(table_name, 0) + records_count
    )

    if all_market and end_date and end_date > manifest.last_dates.get(table_name, ""):
        manifest.last_dates[table_name] = end_date

    if table_name == "prices_daily" and all_market and end_date > manifest.last_daily_date:
        manifest.last_daily_date = end_date

    write_manifest(manifest)
    return manifest

def record_audit_complete(table_name: str, completed_date: str) -> None:
    
    manifest = read_manifest()
    manifest.last_audit_dates[table_name] = completed_date
    write_manifest(manifest)

def record_refresh_complete(table_name: str, tier: str, completed_date: str) -> None:
    
    manifest = read_manifest()
    manifest.last_refresh_dates[f"{table_name}:{tier}"] = completed_date
    write_manifest(manifest)
