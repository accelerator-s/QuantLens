from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from paths import workspace

from .exceptions import MarketRuleNotFoundError, MarketRuleValidationError
from .models import MarketRuleConfig, MarketRuleStatus, validate_transition

_META_FILE = "_meta.yaml"
_LEGACY_FILE = "market_rules.yaml"

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

def registry_path() -> Path:
    
    return workspace().market_rules  

class _MetaDoc(BaseModel):

    version: int = 1
    rules: list[str] = Field(default_factory=list)

class MarketRuleRegistry:

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or registry_path()
        self.path.mkdir(parents=True, exist_ok=True)
        self._maybe_migrate_legacy()

    def list(
        self,
        *,
        status: MarketRuleStatus | None = None,
        market: str | None = None,
        instrument_class: str | None = None,
    ) -> list[MarketRuleConfig]:
        items = self._load_all()
        if status is not None:
            items = [r for r in items if r.status == status]
        if market is not None:
            items = [r for r in items if r.market == market]
        if instrument_class is not None:
            items = [r for r in items if r.instrument_class == instrument_class]
        return sorted(items, key=lambda r: r.name)

    def get(self, name: str) -> MarketRuleConfig:
        rule_file = self._rule_path(name)
        if not rule_file.exists():
            raise MarketRuleNotFoundError(
                code="MARKET_RULE_NOT_FOUND",
                message=f"未注册市场规则：{name}",
                blocking=True,
            )
        return self._load_rule(name)

    def resolve(self, market: str, instrument_class: str) -> MarketRuleConfig:
        
        actives = [
            r
            for r in self._load_all()
            if r.market == market
            and r.instrument_class == instrument_class
            and r.status == MarketRuleStatus.ACTIVE
        ]
        if not actives:
            raise MarketRuleNotFoundError(
                code="MARKET_RULE_NO_ACTIVE",
                message=f"未找到 market={market}, instrument_class={instrument_class} 的 ACTIVE 规则",
                blocking=True,
                suggested_action="请先通过 configure market-rule activate 激活一条规则。",
            )
        if len(actives) > 1:
            names = ", ".join(r.name for r in actives)
            raise MarketRuleValidationError(
                code="MARKET_RULE_AMBIGUOUS",
                message=(
                    f"market={market}, instrument_class={instrument_class} "
                    f"存在多条 ACTIVE 规则：{names}"
                ),
                blocking=True,
                suggested_action="请禁用多余的规则，只保留一条 ACTIVE。",
            )
        return actives[0]

    def upsert(self, rule: MarketRuleConfig, *, override: bool = False) -> None:
        existing_file = self._rule_path(rule.name)
        now = _utcnow()
        rule.updated_at = now

        if existing_file.exists():
            if not override:
                raise MarketRuleValidationError(
                    code="MARKET_RULE_EXISTS",
                    message=f"市场规则 {rule.name} 已存在；如需覆盖请加 --override。",
                    blocking=True,
                )
            existing = self._load_rule(rule.name)
            rule.created_at = existing.created_at
        else:
            rule.created_at = now

        self._save_rule(rule)
        self._rebuild_meta()

    def transition(
        self,
        name: str,
        *,
        target: MarketRuleStatus,
        confirm: bool = False,
    ) -> MarketRuleConfig:
        if target == MarketRuleStatus.ACTIVE and not confirm:
            raise MarketRuleValidationError(
                code="MARKET_RULE_TRANSITION_CONFIRM_REQUIRED",
                message=f"将规则置为 {target.value} 需要 --confirm。",
                blocking=True,
            )
        rule = self.get(name)
        validate_transition(rule.status, target)
        rule.status = target
        rule.updated_at = _utcnow()
        self._save_rule(rule)
        return rule

    def remove(self, name: str, *, confirm: bool = False) -> MarketRuleConfig:
        if not confirm:
            raise MarketRuleValidationError(
                code="MARKET_RULE_REMOVE_CONFIRM_REQUIRED",
                message=f"删除市场规则 {name} 需要 --confirm。",
                blocking=True,
            )
        rule = self.get(name)
        self._rule_path(name).unlink()
        self._rebuild_meta()
        return rule

    # -- 内部方法 --

    @property
    def _meta_path(self) -> Path:
        return self.path / _META_FILE

    def _rule_path(self, name: str) -> Path:
        return self.path / f"{name}.yaml"

    def _load_all(self) -> list[MarketRuleConfig]:
        rules: list[MarketRuleConfig] = []
        for f in sorted(self.path.glob("*.yaml")):
            if f.name == _META_FILE:
                continue
            raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            rules.append(MarketRuleConfig.model_validate(raw))
        return rules

    def _load_rule(self, name: str) -> MarketRuleConfig:
        f = self._rule_path(name)
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        return MarketRuleConfig.model_validate(raw)

    def _save_rule(self, rule: MarketRuleConfig) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self._rule_path(rule.name).write_text(
            yaml.safe_dump(
                rule.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def _rebuild_meta(self) -> None:
        names = sorted(f.stem for f in self.path.glob("*.yaml") if f.name != _META_FILE)
        meta = _MetaDoc(version=1, rules=names)
        self._meta_path.write_text(
            yaml.safe_dump(
                meta.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def _maybe_migrate_legacy(self) -> None:
        
        legacy = self.path.parent / _LEGACY_FILE
        if not legacy.exists():
            return
        rule_files = [f for f in self.path.glob("*.yaml") if f.name != _META_FILE]
        if rule_files:
            return
        raw = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
        for item in raw.get("market_rules", []):
            rule = MarketRuleConfig.model_validate(item)
            self._save_rule(rule)
        self._rebuild_meta()
        legacy.unlink()

__all__ = [
    "MarketRuleRegistry",
    "registry_path",
]
