from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from exceptions import PortfolioContractError
from paths import Workspace, workspace

from .models import (
    HOLDING_COLUMNS,
    TRADE_COLUMNS,
    Plan,
    PlanState,
    Snapshot,
    dump_decimals,
)

if TYPE_CHECKING:
    import pandas as pd

def _require_pandas() -> pd:
    try:
        import pandas as pd  
    except ImportError as exc:  
        raise PortfolioContractError(
            code="portfolio.pandas_missing",
            message="pandas 未安装，无法读写持仓 / 成交 Parquet",
            blocking=True,
            suggested_action="请确认 requirements.txt 中已安装 pandas / pyarrow",
        ) from exc
    return pd

class PortfolioStore:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    def read_holdings(
        self,
        account_id: str,
        *,
        market: str,
        instrument_class: str,
    ) -> pd.DataFrame:
        pd = _require_pandas()
        path = self.ws.portfolio_holdings(market, instrument_class, account_id) / "current.parquet"
        if not path.exists():
            return pd.DataFrame(columns=list(HOLDING_COLUMNS))
        return pd.read_parquet(path)

    def write_holdings(
        self,
        account_id: str,
        df: pd.DataFrame,
        *,
        market: str,
        instrument_class: str,
    ) -> Path:
        
        self._validate_columns(df, HOLDING_COLUMNS, "holdings")
        if not df.empty and (df["account_id"] != account_id).any():
            raise PortfolioContractError(
                code="portfolio.holding_account_mismatch",
                message="holdings 行中存在与目标 account_id 不一致的记录",
                blocking=True,
                suggested_action="请在写入前先按 account_id 过滤",
            )
        target_dir = self.ws.portfolio_holdings(market, instrument_class, account_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "current.parquet"
        tmp = target.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(target)
        return target

    def append_trades(
        self,
        account_id: str,
        df: pd.DataFrame,
        *,
        market: str,
        instrument_class: str,
    ) -> int:
        
        pd = _require_pandas()
        self._validate_columns(df, TRADE_COLUMNS, "trades")
        if df.empty:
            return 0
        if (df["account_id"] != account_id).any():
            raise PortfolioContractError(
                code="portfolio.trade_account_mismatch",
                message="trades 中存在与目标 account_id 不一致的记录",
                blocking=True,
            )

        existing = self.read_trades(account_id, market=market, instrument_class=instrument_class)
        dup = (
            set(existing["trade_id"]).intersection(set(df["trade_id"]))
            if not existing.empty
            else set()
        )
        if dup:
            raise PortfolioContractError(
                code="portfolio.trade_id_duplicate",
                message=f"trade_id 重复：{sorted(dup)[:5]}",
                context={"duplicates": sorted(dup)},
                blocking=True,
                suggested_action="请重新生成 trade_id 或确认是否重复录入",
            )

        df = df.copy()
        df["_month"] = pd.to_datetime(df["executed_at"], utc=True).dt.strftime("%Y-%m")
        target_dir = self.ws.portfolio_trades(market, instrument_class, account_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        added = 0
        for month, group in df.groupby("_month"):
            target = target_dir / f"{month}.parquet"
            group = group.drop(columns=["_month"])
            if target.exists():
                old = pd.read_parquet(target)
                merged = pd.concat([old, group], ignore_index=True)
            else:
                merged = group
            tmp = target.with_suffix(".tmp")
            merged.to_parquet(tmp, index=False)
            tmp.replace(target)
            added += len(group)
        return added

    def read_trades(
        self,
        account_id: str,
        *,
        market: str,
        instrument_class: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        pd = _require_pandas()
        target_dir = self.ws.portfolio_trades(market, instrument_class, account_id)
        if not target_dir.exists():
            return pd.DataFrame(columns=list(TRADE_COLUMNS))
        frames = []
        for parquet in sorted(target_dir.glob("*.parquet")):
            frames.append(pd.read_parquet(parquet))
        if not frames:
            return pd.DataFrame(columns=list(TRADE_COLUMNS))
        df = pd.concat(frames, ignore_index=True)
        if start is not None or end is not None:
            ts = pd.to_datetime(df["executed_at"], utc=True)
            if start is not None:
                df = df[ts >= pd.Timestamp(start, tz="UTC")]
            if end is not None:
                df = df[ts <= pd.Timestamp(end, tz="UTC")]
        return df.reset_index(drop=True)

    def write_trade_audit(
        self,
        account_id: str,
        audit_row: pd.DataFrame,
        *,
        market: str,
        instrument_class: str,
    ) -> Path:
        
        pd = _require_pandas()
        if audit_row.empty:
            raise PortfolioContractError(
                code="portfolio.audit_empty",
                message="审计行为空",
                blocking=True,
            )
        audit_dir = self.ws.portfolio_trades_audit(market, instrument_class, account_id)
        audit_dir.mkdir(parents=True, exist_ok=True)
        month = pd.to_datetime(audit_row["audited_at"], utc=True).dt.strftime("%Y-%m").iloc[0]
        target = audit_dir / f"{month}.parquet"
        if target.exists():
            old = pd.read_parquet(target)
            merged = pd.concat([old, audit_row], ignore_index=True)
        else:
            merged = audit_row
        tmp = target.with_suffix(".tmp")
        merged.to_parquet(tmp, index=False)
        tmp.replace(target)
        return target

    # 快照（YAML）

    def write_snapshot(
        self,
        snap: Snapshot,
        *,
        market: str,
        instrument_class: str,
    ) -> Path:
        target_dir = self.ws.portfolio_snapshots(market, instrument_class, snap.account_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = snap.taken_at.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")
        target = target_dir / f"{ts}.yaml"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(dump_decimals(snap), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(target)
        return target

    def latest_snapshot(
        self,
        account_id: str,
        *,
        market: str,
        instrument_class: str,
    ) -> Snapshot | None:
        target_dir = self.ws.portfolio_snapshots(market, instrument_class, account_id)
        if not target_dir.exists():
            return None
        files = sorted(target_dir.glob("*.yaml"))
        if not files:
            return None
        raw = yaml.safe_load(files[-1].read_text(encoding="utf-8")) or {}
        return Snapshot.model_validate(raw)

    def list_snapshots(
        self,
        account_id: str,
        *,
        market: str,
        instrument_class: str,
        limit: int = 20,
    ) -> list[Path]:
        target_dir = self.ws.portfolio_snapshots(market, instrument_class, account_id)
        if not target_dir.exists():
            return []
        files = sorted(target_dir.glob("*.yaml"), reverse=True)
        return files[:limit]

    def write_plan(self, plan: Plan, *, primary_account: str) -> Path:
        
        if primary_account not in plan.account_ids:
            raise PortfolioContractError(
                code="portfolio.plan_primary_invalid",
                message=f"primary_account={primary_account} 不在 plan.account_ids 中",
                context={"plan_id": plan.plan_id, "account_ids": plan.account_ids},
                blocking=True,
            )
        from .accounts import AccountRegistry  # 避免循环

        registry = AccountRegistry(self.ws)
        account = registry.get(primary_account)
        plans_dir = self.ws.portfolio_plans(
            account.market, account.instrument_class, primary_account
        )
        active_dir = plans_dir / "active"
        active_dir.mkdir(parents=True, exist_ok=True)
        target = active_dir / f"{plan.plan_id}.yaml"
        if target.exists():
            raise PortfolioContractError(
                code="portfolio.plan_id_taken",
                message=f"plan_id 已存在：{plan.plan_id}",
                blocking=True,
            )
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(dump_decimals(plan), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(target)
        return target

    def transition_plan(
        self,
        plan_id: str,
        *,
        primary_account: str,
        new_state: PlanState,
        confirm: bool,
    ) -> Path:
        
        if not confirm:
            raise PortfolioContractError(
                code="portfolio.plan_transition_requires_confirm",
                message="计划状态变更为破坏性操作，必须 confirm=True",
                blocking=True,
            )
        from .accounts import AccountRegistry  # 避免循环

        registry = AccountRegistry(self.ws)
        account = registry.get(primary_account)
        plans_dir = self.ws.portfolio_plans(
            account.market, account.instrument_class, primary_account
        )
        active_path = plans_dir / "active" / f"{plan_id}.yaml"
        if not active_path.exists():
            raise PortfolioContractError(
                code="portfolio.plan_not_found",
                message=f"未找到 active 计划：{plan_id}",
                blocking=True,
            )
        raw = yaml.safe_load(active_path.read_text(encoding="utf-8")) or {}
        raw["state"] = new_state.value
        if new_state in (PlanState.EXECUTED, PlanState.ARCHIVED, PlanState.CANCELLED):
            month = datetime.now(UTC).strftime("%Y-%m")
            archive_dir = plans_dir / "archive" / month
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / f"{plan_id}.yaml"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            tmp.replace(target)
            active_path.unlink()
            return target
        tmp = active_path.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(active_path)
        return active_path

    def find_plan(self, plan_id: str, *, primary_account: str) -> tuple[Path, dict, str]:
        
        from .accounts import AccountRegistry  # 避免循环

        account = AccountRegistry(self.ws).get(primary_account)
        plans_dir = self.ws.portfolio_plans(
            account.market, account.instrument_class, primary_account
        )
        active_path = plans_dir / "active" / f"{plan_id}.yaml"
        if active_path.exists():
            raw = yaml.safe_load(active_path.read_text(encoding="utf-8")) or {}
            return active_path, raw, "active"
        archive_dir = plans_dir / "archive"
        if archive_dir.exists():
            for path in sorted(archive_dir.glob(f"*/{plan_id}.yaml"), reverse=True):
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                return path, raw, "archive"
        raise PortfolioContractError(
            code="portfolio.plan_not_found",
            message=f"未找到计划：{plan_id}",
            context={"plan_id": plan_id, "primary_account": primary_account},
            blocking=True,
            suggested_action="请核对 plan_id 与主账户；`portfolio plan list` 可查看现有计划",
        )

    def list_active_plans(self, *, primary_account: str) -> list[tuple[Path, dict]]:
        
        from .accounts import AccountRegistry  # 避免循环

        account = AccountRegistry(self.ws).get(primary_account)
        active_dir = (
            self.ws.portfolio_plans(account.market, account.instrument_class, primary_account)
            / "active"
        )
        if not active_dir.exists():
            return []
        return [
            (path, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
            for path in sorted(active_dir.glob("*.yaml"))
        ]

    def update_plan_annotation(
        self,
        plan_id: str,
        *,
        primary_account: str,
        trigger_reason: str,
        extra_caveats: list[str] | None = None,
    ) -> Path:
        
        path, raw, bucket = self.find_plan(plan_id, primary_account=primary_account)
        if bucket != "active":
            raise PortfolioContractError(
                code="portfolio.plan_annotation_not_active",
                message=f"仅 active 目录中的计划可回写 AI 注解：{plan_id}（当前 {bucket}）",
                blocking=True,
                suggested_action="已归档计划为不可变档案，不支持再注解",
            )
        raw["trigger_reason"] = trigger_reason
        if extra_caveats:
            raw["caveats"] = list(raw.get("caveats") or []) + list(extra_caveats)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    # 重新初始化日志

    def append_reinit_log(
        self,
        account_id: str,
        *,
        market: str,
        instrument_class: str,
        entry: dict,
    ) -> Path:
        target = self.ws.portfolio_account(market, instrument_class, account_id) / "reinit_log.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else None
        entries = list((existing or {}).get("entries") or [])
        entries.append(entry)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump({"entries": entries}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(target)
        return target

    # 内部

    @staticmethod
    def _validate_columns(df: pd.DataFrame, expected: tuple[str, ...], label: str) -> None:
        missing = [c for c in expected if c not in df.columns]
        if missing:
            raise PortfolioContractError(
                code=f"portfolio.{label}_columns_missing",
                message=f"{label} DataFrame 缺少列：{missing}",
                context={"expected": list(expected), "missing": missing},
                blocking=True,
                suggested_action="请按持仓页面显示的表头补齐列",
            )

__all__ = ["PortfolioStore"]
