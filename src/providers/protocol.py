from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

@dataclass(frozen=True)
class UpdateStatus:

    needs_update: bool
    last_date: str | None
    pending_days: int
    estimated_rows: int
    available_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    batch_count: int = 0

    def summary_lines(self, scope: str = "全市场") -> list[str]:
        
        if self.last_date is None:
            return ["[bold]首次下载[/bold]：尚无历史数据，将执行全量下载"]
        return [
            f"范围：{scope}",
            f"最后更新：{self.last_date}",
            f"待更新：{self.pending_days} 天（{self.start_date} → {self.end_date}）",
            f"预估行数：~{self.estimated_rows:,} 条",
            f"分片数：{self.batch_count} 个批次",
        ]

@runtime_checkable
class DataProvider(Protocol):

    @property
    def name(self) -> str: ...

    @property
    def market(self) -> str: ...

    @property
    def instrument_class(self) -> str: ...

    def check_update_needed(self) -> UpdateStatus: ...

    def download_incremental(self, *, instrument_codes: list[str] | None = None) -> int: ...

    def download_full(
        self,
        start_date: str,
        end_date: str,
        *,
        instrument_codes: list[str] | None = None,
    ) -> tuple[pd.DataFrame, dict]: ...

    def append_to_staged(self, df: pd.DataFrame, table_name: str = "prices_daily") -> int: ...

    def append_download_record(self, **record_info: object) -> None: ...
