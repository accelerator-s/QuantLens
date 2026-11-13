from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from exceptions import QuantTrainingError
from paths import workspace

from .config import EvaluationConfig


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()

def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

class RunStore:

    def __init__(self, run_dir: Path, config: EvaluationConfig) -> None:
        self.run_dir = run_dir
        self.config = config

    @classmethod
    def create(cls, config: EvaluationConfig, *, extra_meta: dict | None = None) -> RunStore:
        ws = workspace()
        run_id = datetime.now(tz=UTC).strftime("EVAL-%Y%m%d-%H%M%S")
        run_dir = ws.runs / "factor_eval" / config.factor_name / run_id
        if run_dir.exists():  
            n = 2
            while (ws.runs / "factor_eval" / config.factor_name / f"{run_id}-{n}").exists():
                n += 1
            run_dir = ws.runs / "factor_eval" / config.factor_name / f"{run_id}-{n}"
        run_dir.mkdir(parents=True)
        store = cls(run_dir, config)
        _write_yaml(
            run_dir / "config.yaml",
            {
                "config": config.model_dump(mode="json"),
                "config_hash": config.content_hash(),
                "created_at": _utcnow_iso(),
                **(extra_meta or {}),
            },
        )
        store.update_status(state="running")
        return store

    @classmethod
    def open_resume(cls, config: EvaluationConfig, run_id: str) -> RunStore:
        ws = workspace()
        run_dir = ws.runs / "factor_eval" / config.factor_name / run_id
        cfg_file = run_dir / "config.yaml"
        if not cfg_file.exists():
            raise QuantTrainingError(
                code="EVALUATION_RUN_NOT_FOUND",
                message=f"找不到可续算的评价运行：{run_id}。",
                context={"run_dir": str(run_dir)},
                blocking=True,
                suggested_action="确认 run_id（runs/factor_eval/{factor}/ 下的目录名）。",
            )
        recorded = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        if recorded.get("config_hash") != config.content_hash():
            raise QuantTrainingError(
                code="EVALUATION_RESUME_CONFIG_MISMATCH",
                message="续算配置与原运行配置不一致，拒绝续算（避免混合口径的报告）。",
                context={
                    "recorded_hash": recorded.get("config_hash"),
                    "current_hash": config.content_hash(),
                },
                blocking=True,
                suggested_action="使用与原运行完全相同的评价参数，或去掉 --resume 开新运行。",
            )
        return cls(run_dir, config)

    @property
    def run_id(self) -> str:
        return self.run_dir.name

    def update_status(self, *, state: str, extra: dict | None = None) -> None:
        _write_yaml(
            self.run_dir / "status.yaml",
            {"state": state, "updated_at": _utcnow_iso(), **(extra or {})},
        )

    def write_metrics(self, metrics: dict) -> None:
        _write_yaml(self.run_dir / "metrics.yaml", metrics)

__all__ = ["RunStore"]
