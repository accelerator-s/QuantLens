from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from exceptions import QuantTrainingError
from paths import Workspace, workspace
from security import confined_path

from .models import EvolutionConfig, EvolutionStatus, FactorVersion

def _now() -> str:
    return datetime.now(tz=UTC).isoformat()

def _research_id() -> str:
    return f"EVO-{datetime.now(tz=UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2).upper()}"

class EvolutionStore:
    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    @property
    def root(self) -> Path:
        return self.ws.runs / "factor_evolution"

    def create(self, config: EvolutionConfig) -> EvolutionStatus:
        research_id = _research_id()
        run_dir = self.root / research_id
        run_dir.mkdir(parents=True, exist_ok=False)
        self._write_json(run_dir / "config.json", config.model_dump(mode="json"))
        status = EvolutionStatus(research_id=research_id, updated_at=_now())
        self.save_status(status)
        self.append_event(research_id, "session_created", {"config_hash": config.content_hash()})
        return status

    def run_dir(self, research_id: str) -> Path:
        path = confined_path(self.root, research_id, "")
        if not path.is_dir():
            raise QuantTrainingError(
                code="EVOLUTION_NOT_FOUND",
                message=f"未找到自动进化会话：{research_id}",
                blocking=True,
                suggested_action="用 `factor evolve list` 查看已有会话。",
            )
        return path

    def load_config(self, research_id: str) -> EvolutionConfig:
        return EvolutionConfig.model_validate(
            self._read_json(self.run_dir(research_id) / "config.json")
        )

    def load_status(self, research_id: str) -> EvolutionStatus:
        return EvolutionStatus.model_validate(
            self._read_json(self.run_dir(research_id) / "status.json")
        )

    def save_status(self, status: EvolutionStatus) -> None:
        import fcntl

        run_dir = self.run_dir(status.research_id)
        path = run_dir / "status.json"
        with (run_dir / "status.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            # A worker's older in-memory status must never erase cancellation.
            if path.exists() and self._read_json(path).get("cancel_requested"):
                status.cancel_requested = True
            status.updated_at = _now()
            self._write_json(path, status.model_dump(mode="json"))

    def append_event(self, research_id: str, event_type: str, payload: dict[str, Any]) -> None:
        record = {"created_at": _now(), "event_type": event_type, "payload": payload}
        path = self.run_dir(research_id) / "events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def append_memory(self, research_id: str, payload: dict[str, Any]) -> None:
        record = {"created_at": _now(), **payload}
        path = self.run_dir(research_id) / "session_memory.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def memories(self, research_id: str) -> list[dict[str, Any]]:
        path = self.run_dir(research_id) / "session_memory.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def save_version(self, research_id: str, version: FactorVersion) -> Path:
        path = confined_path(self.run_dir(research_id) / "versions", version.version_id, ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, version.model_dump(mode="json"))
        return path

    def load_version(self, research_id: str, version_id: str) -> FactorVersion:
        path = confined_path(self.run_dir(research_id) / "versions", version_id, ".json")
        if not path.exists():
            raise QuantTrainingError(
                code="EVOLUTION_VERSION_NOT_FOUND",
                message=f"会话 {research_id} 中没有版本 {version_id}",
                blocking=True,
            )
        return FactorVersion.model_validate(self._read_json(path))

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            try:
                status = EvolutionStatus.model_validate(self._read_json(path / "status.json"))
                config = EvolutionConfig.model_validate(self._read_json(path / "config.json"))
            except Exception:
                continue
            rows.append(
                {
                    **status.model_dump(mode="json"),
                    "base_factor": config.base_factor,
                    "profile_name": config.profile_name,
                    "model_name": config.model_name,
                }
            )
        return rows

    def detail(self, research_id: str) -> dict[str, Any]:
        run_dir = self.run_dir(research_id)
        versions = []
        for path in (
            sorted((run_dir / "versions").glob("*.json")) if (run_dir / "versions").exists() else []
        ):
            versions.append(self._read_json(path))
        events_path = run_dir / "events.jsonl"
        events = (
            [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            if events_path.exists()
            else []
        )
        return {
            "config": self.load_config(research_id).model_dump(mode="json"),
            "status": self.load_status(research_id).model_dump(mode="json"),
            "versions": versions,
            "events": events,
            "memory": self.memories(research_id),
            "run_dir": str(run_dir),
        }

    def request_cancel(self, research_id: str) -> EvolutionStatus:
        status = self.load_status(research_id)
        status.cancel_requested = True
        self.save_status(status)
        self.append_event(research_id, "cancel_requested", {})
        return status

    def write_report(self, research_id: str) -> Path:
        detail = self.detail(research_id)
        config = detail["config"]
        status = detail["status"]
        locked = [
            event
            for event in detail["events"]
            if event["event_type"] in {"locked_test_completed", "locked_test_failed"}
        ]
        evaluated = [
            event for event in detail["events"] if event["event_type"] == "version_evaluated"
        ]
        best_evaluation = next(
            (
                event["payload"]
                for event in reversed(evaluated)
                if event["payload"].get("version_id") == status["best_version_id"]
            ),
            evaluated[-1]["payload"] if evaluated else {},
        )
        generalization = best_evaluation.get("generalization") or {}
        locked_completed = [
            event for event in locked if event["event_type"] == "locked_test_completed"
        ]
        final_classification = (
            locked_completed[-1]["payload"].get("generalization_classification")
            if locked_completed
            else generalization.get("classification")
        )
        model_dispatches = [
            event for event in detail["events"] if event["event_type"] == "model_context_dispatched"
        ]
        from presentation import factor_display_name, strategy_display_name

        lines = [
            f"# 单因子自动进化报告：{research_id}",
            "",
            f"- 基础因子：{factor_display_name(config['base_factor'])}",
            f"- 研究规则：{strategy_display_name(config['profile_name'])}",
            f"- 训练区间：{config['train_window']['start']}..{config['train_window']['end']}",
            f"- 验证区间：{config['validation_window']['start']}..{config['validation_window']['end']}",
            f"- 锁定测试区间：{config['test_window']['start']}..{config['test_window']['end']}",
            f"- 最终决策：{status['decision']}",
            f"- 结束原因：{status['terminal_reason'] or status['error']}",
            f"- 最佳版本：{status['best_version_id'] or '无'}",
            f"- 实际迭代：{status['iteration']}（评价成功 {status['successful_rounds']}，"
            f"轮内失败 {status['failed_rounds']}）",
            f"- 锁定测试次数：{len(locked)}",
            f"- 滚动验证：{'启用' if config['walk_forward']['enabled'] else '未启用'}"
            f"（{config['walk_forward']['mode']}）",
            f"- 泛化分类：{final_classification or '尚未评价'}",
            f"- 训练收益中位数：{generalization.get('train_median_excess_return', '—')}",
            f"- 验证收益中位数：{generalization.get('validation_median_excess_return', '—')}",
            f"- 近期加权验证收益："
            f"{generalization.get('recency_weighted_validation_excess_return', '—')}",
            f"- 最新一折验证收益：{generalization.get('latest_validation_excess_return', '—')}",
            f"- 最新一折是否达标：{generalization.get('latest_fold_passed', '—')}",
            f"- 近期衰减：{generalization.get('recent_degradation', '—')}",
            f"- 训练到验证的泛化落差：{generalization.get('generalization_gap', '—')}",
            f"- 验证正收益折占比：{generalization.get('validation_positive_fold_ratio', '—')}",
            f"- 模型上下文派发次数：{len(model_dispatches)}；锁定测试结果派发次数："
            f"{sum(bool(event['payload'].get('locked_test_visible')) for event in model_dispatches)}",
            "",
            "## 滚动验证路径",
            "",
            "| fold | 训练区间 | 验证区间 | 训练 run | 验证 run |",
            "| --- | --- | --- | --- | --- |",
        ]
        for fold in best_evaluation.get("folds") or []:
            lines.append(
                f"| {fold['fold_id']} | {fold['train_window']['start']}.."
                f"{fold['train_window']['end']} | {fold['validation_window']['start']}.."
                f"{fold['validation_window']['end']} | {fold['train']['run_id']} | "
                f"{fold['validation']['run_id']} |"
            )
        lines.extend(
            [
                "",
                "## 版本路径",
                "",
                "| 版本 | 父版本 | 改动类型 | 改动摘要 | proposal |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for version in detail["versions"]:
            lines.append(
                "| {version_id} | {parent} | {change_type} | {summary} | {proposal} |".format(
                    version_id=version["version_id"],
                    parent=version.get("parent_version_id") or "—",
                    change_type=version["change_type"],
                    summary=str(version["change_summary"]).replace("|", "\\|"),
                    proposal=version.get("proposal_id") or "—",
                )
            )
        lines.extend(
            [
                "",
                "## 说明",
                "",
                "所有数值以对应 backtest run 产物为准；完整事实流见 events.jsonl，",
                "本报告不构成未来收益保证，冻结版本也不自动获得生产权限。",
                "",
            ]
        )
        path = self.run_dir(research_id) / "report.json"
        path.write_text(json.dumps({"content":"\n".join(lines)},ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        temp.replace(path)

__all__ = ["EvolutionStore"]
