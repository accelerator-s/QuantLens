from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from exceptions import QuantTrainingError
from paths import workspace

class AutomationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    daily_time: str = "18:30"
    timezone: str = "Asia/Shanghai"
    account_ids: list[str] = Field(default_factory=list)
    sync_data: bool = True
    run_shadow_labels: bool = True
    run_attribution: bool = True
    run_decay_monitor: bool = True

    @field_validator("daily_time")
    @classmethod
    def _time(cls, value: str) -> str:
        try:
            hour, minute = (int(part) for part in value.split(":"))
        except (ValueError, TypeError) as exc:
            raise ValueError("daily_time 必须为 HH:MM") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("daily_time 必须为合法 HH:MM")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

class AutomationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or workspace().config / "automation.yaml"

    def load(self) -> AutomationConfig:
        if not self.path.exists():
            return AutomationConfig()
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return AutomationConfig.model_validate(raw)

    def save(self, config: AutomationConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".yaml.tmp")
        temporary.write_text(
            yaml.safe_dump(
                config.model_dump(mode="json"), allow_unicode=True, sort_keys=False
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

class AutomationScheduler:
    def __init__(self, manager) -> None:
        self.manager = manager
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_date = ""
        self._last_config_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="quant-daily-automation", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.wait(30):
            try:
                config = AutomationStore().load()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if message != self._last_config_error:
                    self._last_config_error = message
                    self.manager.start(
                        "daily-automation:config-error",
                        lambda _reporter, detail=message: self._raise_config(detail),
                        resource="daily-automation-config",
                    )
                continue
            self._last_config_error = ""
            if not config.enabled:
                continue
            now = datetime.now(ZoneInfo(config.timezone))
            day = now.date().isoformat()
            if now.strftime("%H:%M") < config.daily_time or day == self._last_date:
                continue
            try:
                self.manager.start(
                    f"daily-automation:{day}",
                    lambda reporter: self._run(config, reporter),
                    resource="daily-automation",
                )
            except QuantTrainingError:
                continue
            self._last_date = day

    @staticmethod
    def _raise_config(detail: str):
        raise QuantTrainingError(
            code="DAILY_AUTOMATION_CONFIG_INVALID",
            message=f"每日自动化配置无效：{detail}",
            blocking=True,
            suggested_action="在 WebUI 重新保存合法的时间、时区和账户列表。",
        )

    @staticmethod
    def _run(config: AutomationConfig, reporter) -> dict:
        from lifecycle.attribution import AttributionService
        from lifecycle.daily import DailyOperationsService
        from lifecycle.decay import DecayMonitor
        from lifecycle.models import DataSnapshot, ObjectType
        from lifecycle.store import LifecycleStore

        output = {}
        if config.sync_data:
            from quantlens.sync import run_sync
            from webui.backend.routes.data_ops import _active_provider, _log_console

            provider = _active_provider("")
            report = run_sync(
                provider,
                console=_log_console(reporter),
                yes=True,
                on_progress=reporter.progress,
            )
            if not report.ok:
                if report.error:
                    raise report.error
                raise QuantTrainingError(
                    code="DAILY_AUTOMATION_SYNC_FAILED",
                    message=f"自动数据更新失败：{report.failed_step}",
                    blocking=True,
                    suggested_action="在数据页查看同一步骤的结构化错误后重试。",
                )
            output["data_sync"] = {"new_rows": report.new_rows}
        daily = DailyOperationsService().run(account_ids=config.account_ids or None)
        output["daily"] = daily
        snapshot = LifecycleStore().get(
            ObjectType.DATA_SNAPSHOT, daily["data_snapshot_id"]
        )
        assert isinstance(snapshot, DataSnapshot)
        as_of = snapshot.as_of.date().isoformat()
        if config.run_shadow_labels:
            output["shadow"] = DailyOperationsService().observe_matured_shadows(
                as_of=as_of
            )
        if config.run_attribution:
            output["attribution"] = AttributionService().mature(as_of=as_of)
        if config.run_decay_monitor:
            decay = DecayMonitor().scan()
            output["decay"] = decay
            triggered = []
            trigger_errors = []
            for event in decay["trigger_candidates"]:
                if event.trigger_research_loop is None:
                    continue
                try:
                    source = event.source_object_refs[-1]
                    attribution = LifecycleStore().get(
                        source.object_type, source.object_id
                    )
                    strategy = LifecycleStore().get(
                        ObjectType.STRATEGY_VERSION,
                        attribution.strategy_version_id,
                    )
                    from webui.backend.routes.strategy_evolution import (
                        StartPayload,
                        start_strategy_evolution,
                    )

                    started = start_strategy_evolution(
                        StartPayload(
                            profile_name=strategy.strategy_name,
                            trigger_learning_event_id=event.learning_event_id,
                        )
                    )
                    triggered.append(
                        {
                            "learning_event_id": event.learning_event_id,
                            "program_id": started["program_id"],
                            "task_id": started["task_id"],
                        }
                    )
                except QuantTrainingError as exc:
                    trigger_errors.append(
                        {
                            "learning_event_id": event.learning_event_id,
                            "code": exc.code,
                            "message": exc.message,
                            "suggested_action": exc.suggested_action,
                        }
                    )
            output["decay"]["triggered_research"] = triggered
            output["decay"]["trigger_errors"] = trigger_errors
        return output

__all__ = ["AutomationConfig", "AutomationScheduler", "AutomationStore"]
