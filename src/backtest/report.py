from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re

import pandas as pd
import yaml

from exceptions import BacktestError
from paths import workspace

def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()

def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

class BacktestRunStore:

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @classmethod
    def create(
        cls,
        *,
        profile_name: str,
        config_payload: dict,
        run_id: str | None = None,
    ) -> BacktestRunStore:
        base = workspace().runs_backtest / profile_name
        if run_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
            raise BacktestError(
                code="BACKTEST_RUN_ID_INVALID",
                message="预留的回测 run_id 含非法字符或长度超限。",
                blocking=True,
                suggested_action="仅使用字母、数字、点、下划线和连字符生成 run_id。",
            )
        run_id = run_id or datetime.now(tz=UTC).strftime("BT-%Y%m%d-%H%M%S")
        run_dir = base / run_id
        if run_dir.exists() and run_id.startswith("BT-") and len(run_id) == 18:
            
            n = 2
            while (base / f"{run_id}-{n}").exists():
                n += 1
            run_dir = base / f"{run_id}-{n}"
        elif run_dir.exists():
            raise BacktestError(
                code="BACKTEST_RESERVED_RUN_EXISTS",
                message=f"预留回测目录已存在：{run_dir}",
                blocking=True,
                suggested_action="普通运行创建新的 attempt；锁定测试不得重复执行。",
            )
        run_dir.mkdir(parents=True)
        store = cls(run_dir)
        _write_yaml(run_dir / "config.yaml", {**config_payload, "created_at": _utcnow_iso()})
        store.update_status(state="running")
        return store

    @property
    def run_id(self) -> str:
        return self.run_dir.name

    def update_status(self, *, state: str, extra: dict | None = None) -> None:
        _write_yaml(
            self.run_dir / "status.yaml",
            {"state": state, "updated_at": _utcnow_iso(), **(extra or {})},
        )

    def write_daily(self, name: str, frame: pd.DataFrame) -> None:
        
        daily = self.run_dir / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(daily / f"{name}.parquet", index=False)

    def write_metrics(self, metrics: dict) -> None:
        _write_yaml(self.run_dir / "metrics.yaml", metrics)

    def delete(self) -> None:
        """Remove an erroneous local attempt permanently, without a tombstone."""
        import shutil
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir)

    def _require_valid(self) -> None:
        if not self.run_dir.is_dir():
            raise BacktestError(code='BACKTEST_RUN_NOT_FOUND',
                message=f'找不到回测运行：{self.run_id}。',blocking=True)

    def write_chart(self, name: str, frame: pd.DataFrame) -> None:
        charts = self.run_dir / "charts"
        charts.mkdir(parents=True, exist_ok=True)
        out = frame.copy()
        out.index = out.index.astype(str)
        out.to_csv(charts / f"{name}.csv", float_format="%.8g")

    @staticmethod
    def list_runs(profile_name: str | None = None) -> list[dict]:
        
        base = workspace().runs_backtest
        if not base.exists():
            return []
        profiles = [base / profile_name] if profile_name else sorted(base.iterdir())
        rows: list[dict] = []
        for pdir in profiles:
            if not pdir.is_dir():
                continue
            for run_dir in sorted(pdir.iterdir(), reverse=True):
                status_file = run_dir / "status.yaml"
                if not status_file.exists():
                    continue
                status = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
                cfg = {}
                cfg_file = run_dir / "config.yaml"
                if cfg_file.exists():
                    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
                rows.append(
                    {
                        "profile": pdir.name,
                        "run_id": run_dir.name,
                        "state": status.get("state", "?"),
                        "start": (cfg.get("config") or {}).get("start", "?"),
                        "end": (cfg.get("config") or {}).get("end", "?"),
                        "protocol_locked": cfg.get("protocol_locked"),
                        "run_dir": str(run_dir),
                    }
                )
        rows.sort(key=lambda r: r["run_id"], reverse=True)
        return rows

    @staticmethod
    def open_run(run_id: str) -> BacktestRunStore:
        
        base = workspace().runs_backtest
        matches = sorted(base.glob(f"*/{run_id}")) if base.exists() else []
        if not matches:
            raise BacktestError(
                code="BACKTEST_RUN_NOT_FOUND",
                message=f"找不到回测运行：{run_id}。",
                blocking=True,
                suggested_action="用 `backtest list` 查看可用 run_id。",
            )
        return BacktestRunStore(matches[0])

    def read_config(self) -> dict:
        path = self.run_dir / "config.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}

    def read_metrics(self) -> dict:
        self._require_valid()
        path = self.run_dir / "metrics.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}

    def read_status(self) -> dict:
        path = self.run_dir / "status.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}

    def read_daily(self, name: str) -> pd.DataFrame:
        self._require_valid()
        path = self.run_dir / "daily" / f"{name}.parquet"
        if not path.exists():
            raise BacktestError(
                code="BACKTEST_RUN_DATA_MISSING",
                message=f"运行 {self.run_id} 缺少日频数据：daily/{name}.parquet。",
                blocking=True,
                suggested_action="该运行可能未完成（status.yaml state≠completed），重跑回测。",
            )
        return pd.read_parquet(path)

__all__ = ["BacktestRunStore"]
