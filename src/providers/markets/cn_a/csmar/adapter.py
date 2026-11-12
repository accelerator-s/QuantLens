from __future__ import annotations

import pandas as pd

from providers.protocol import UpdateStatus

class CSMARDataProvider:

    @property
    def name(self) -> str:
        return "csmar"

    @property
    def market(self) -> str:
        return "CN-A"

    @property
    def instrument_class(self) -> str:
        return "stock"

    def check_update_needed(self, table_name: str = "prices_daily") -> UpdateStatus:
        from .incremental import check_update_needed

        return check_update_needed(table_name)

    def download_incremental(self, *, instrument_codes: list[str] | None = None) -> int:
        from .incremental import download_incremental

        stats = download_incremental(instrument_codes=instrument_codes)
        return stats.new_rows if stats is not None else 0

    def list_tables(self) -> list[dict]:
        from .tables import table_summaries

        return table_summaries()

    def download_table_incremental(
        self,
        table_name: str,
        *,
        instrument_codes: list[str] | None = None,
        force_audit: bool = False,
        skip_audit: bool = False,
    ) -> int:
        from .incremental import download_incremental

        stats = download_incremental(
            table_name=table_name,
            instrument_codes=instrument_codes,
            force_audit=force_audit,
            skip_audit=skip_audit,
        )
        return stats.new_rows if stats is not None else 0

    def download_table_batched(
        self,
        table_name: str,
        start_date: str,
        end_date: str,
        *,
        instrument_codes: list[str] | None = None,
    ) -> int:
        from .incremental import download_table_batched

        stats = download_table_batched(
            table_name,
            start_date,
            end_date,
            instrument_codes=instrument_codes,
        )
        return stats.new_rows

    def plan_update_all(self, *, force_audit: bool = False, skip_audit: bool = False):
        
        from .orchestrator import plan_auto_update

        return plan_auto_update(force_audit=force_audit, skip_audit=skip_audit)

    def update_all(
        self,
        *,
        force_audit: bool = False,
        skip_audit: bool = False,
        console=None,
        on_progress=None,
    ):
        
        from .orchestrator import run_auto_update

        return run_auto_update(
            force_audit=force_audit,
            skip_audit=skip_audit,
            console=console,
            on_progress=on_progress,
        )

    def run_integrity_checks(self):
        
        from .integrity import run_integrity_checks

        return run_integrity_checks()

    def attach_ann_dates(self) -> dict:
        from .fundamentals import attach_ann_dates

        return attach_ann_dates()

    def attach_holder_data(self) -> dict:
        
        from .holders import attach_holder_data

        return attach_holder_data()

    def attach_analyst_data(self) -> dict:
        
        from .analyst import attach_analyst_data

        return attach_analyst_data()

    def download_full(
        self,
        start_date: str,
        end_date: str,
        *,
        instrument_codes: list[str] | None = None,
    ) -> tuple[pd.DataFrame, dict]:
        from .incremental import download_full

        return download_full(start_date, end_date, instrument_codes=instrument_codes)

    def append_to_staged(self, df: pd.DataFrame, table_name: str = "prices_daily") -> int:
        from .incremental import append_to_staged

        return append_to_staged(df, table_name)

    def append_download_record(self, **record_info: object) -> None:
        from .manifest import append_download_record

        append_download_record(**record_info)
