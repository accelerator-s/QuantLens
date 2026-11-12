from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from exceptions import QuantTrainingError, StrategyProfileError
from paths import Workspace, workspace

from .models import (
    ResolvedStrategyProfile,
    StrategyProfileDoc,
    StrategyStatus,
    resolve_profile,
    validate_transition,
)

_META_FILE = "_meta.yaml"

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

def store_path() -> Path:
    
    return workspace().strategies  

class _MetaDoc(BaseModel):

    version: int = 1
    profiles: list[str] = Field(default_factory=list)

def _format_validation_error(exc: ValidationError) -> str:
    
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"{loc}: {err['msg']}")
    return "；".join(lines)

class StrategyProfileStore:

    def __init__(self, path: Path | None = None, *, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()
        self.path = path or self.ws.strategies
        self.path.mkdir(parents=True, exist_ok=True)

    def list(
        self,
        *,
        status: StrategyStatus | None = None,
        templates_only: bool | None = None,
    ) -> list[StrategyProfileDoc]:
        items = self._load_all()
        if status is not None:
            items = [d for d in items if d.status == status]
        if templates_only is not None:
            items = [d for d in items if d.is_template == templates_only]
        return sorted(items, key=lambda d: d.profile_name)

    def get(self, name: str) -> StrategyProfileDoc:
        f = self._profile_path(name)
        if not f.exists():
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_NOT_FOUND",
                message=f"策略配置不存在：{name}",
                blocking=True,
                suggested_action="用 `configure strategy list` 查看已有配置。",
            )
        return self._load_doc(f)

    def resolve(self, name: str) -> ResolvedStrategyProfile:
        
        return resolve_profile(self.get(name), self.get)

    def upsert(self, doc: StrategyProfileDoc, *, override: bool = False) -> None:
        existing_file = self._profile_path(doc.profile_name)
        now = _utcnow()
        doc.updated_at = now

        if existing_file.exists():
            if not override:
                raise StrategyProfileError(
                    code="STRATEGY_PROFILE_EXISTS",
                    message=f"策略配置 {doc.profile_name} 已存在；如需覆盖请加 --override。",
                    blocking=True,
                )
            existing = self._load_doc(existing_file)
            if existing.status == StrategyStatus.ACTIVE:
                raise StrategyProfileError(
                    code="STRATEGY_PROFILE_ACTIVE_IMMUTABLE",
                    message=f"策略配置 {doc.profile_name} 处于 active 状态，禁止直接覆盖。",
                    blocking=True,
                    suggested_action="先 `configure strategy disable --confirm` 停用，修改后重新激活。",
                )
            doc.created_at = existing.created_at
            doc.status = existing.status
        else:
            doc.created_at = now

        if doc.inheritance_parent:
            if doc.inheritance_parent == doc.profile_name:
                raise StrategyProfileError(
                    code="STRATEGY_PROFILE_INHERIT_CYCLE",
                    message=f"策略配置 {doc.profile_name} 不能继承自身。",
                    blocking=True,
                )
            self._require_parent(doc)

        self._save_doc(doc)
        self._rebuild_meta()

    def validate_for_activation(self, name: str) -> list[str]:
        
        doc = self.get(name)
        if doc.is_template:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_TEMPLATE_NOT_ACTIVATABLE",
                message=f"{name} 是基础模板（is_template=true），不允许激活。",
                blocking=True,
                suggested_action="创建 inheritance_parent 指向该模板的派生策略并激活派生策略。",
            )
        resolved = resolve_profile(doc, self.get)
        warnings: list[str] = []

        from factors.registry import FactorRegistry, FactorStatus

        registry = FactorRegistry()
        missing_factors: list[str] = []
        for ref in resolved.factors.items:
            try:
                meta = registry.get(ref.name)
            except QuantTrainingError:
                missing_factors.append(ref.name)
                continue
            if meta.status != FactorStatus.ACTIVE:
                warnings.append(
                    f"因子 {ref.name} 状态为 {meta.status.value}（未经 factor issue 验收启用）。"
                )
        if missing_factors:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_FACTOR_NOT_FOUND",
                message=(
                    f"策略配置 {name} 引用了未注册因子：{'、'.join(missing_factors)}，拒绝生效。"
                ),
                context={"profile": name, "missing_factors": missing_factors},
                blocking=True,
                suggested_action="先通过 factor issue 落地并注册相关因子，再激活策略。",
                related_subjects={"strategy_profile": name},
            )

        from market_rules import MarketRuleRegistry, MarketRuleStatus

        rule_name = resolved.trading_rules.market_rule
        try:
            rule = MarketRuleRegistry().get(rule_name)
        except QuantTrainingError as exc:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_MARKET_RULE_UNRESOLVED",
                message=f"策略配置 {name} 引用的市场规则 {rule_name} 不存在。",
                context={"profile": name, "market_rule": rule_name},
                blocking=True,
                suggested_action="用 `configure market-rule list` 核对规则名，或先加载并激活规则。",
            ) from exc
        if rule.status != MarketRuleStatus.ACTIVE:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_MARKET_RULE_UNRESOLVED",
                message=(
                    f"策略配置 {name} 引用的市场规则 {rule_name} "
                    f"状态为 {rule.status.value}（需 ACTIVE）。"
                ),
                blocking=True,
                suggested_action="先 `configure market-rule activate` 激活该规则。",
            )

        from portfolio.accounts import AccountRegistry

        accounts = AccountRegistry().list(
            market=resolved.market, instrument_class=rule.instrument_class
        )
        if not accounts:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_NO_ACCOUNT",
                message=(
                    f"策略配置 {name} 面向市场 {resolved.market}（{rule.instrument_class}），"
                    "尚无对应账户，无法激活实盘。"
                ),
                blocking=True,
                suggested_action=(
                    f"先用 `account create` 创建 {resolved.market} 市场的 "
                    f"{rule.instrument_class} 账户，再激活该策略。"
                ),
                related_subjects={"strategy_profile": name},
            )

        return warnings

    def transition(
        self,
        name: str,
        *,
        target: StrategyStatus,
        confirm: bool = False,
    ) -> StrategyProfileDoc:
        if target == StrategyStatus.ACTIVE and not confirm:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_TRANSITION_CONFIRM_REQUIRED",
                message=f"将策略配置置为 {target.value} 需要 --confirm（人工确认）。",
                blocking=True,
            )
        doc = self.get(name)
        validate_transition(doc.status, target)
        if target == StrategyStatus.ACTIVE:
            
            self.validate_for_activation(name)
        doc.status = target
        doc.updated_at = _utcnow()
        self._save_doc(doc)
        return doc

    def delete(
        self,
        name: str,
        *,
        confirm: bool = False,
        reason: str = "",
        deleted_by: str = "cli",
    ) -> Path:
        
        if not confirm:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_DELETE_CONFIRM_REQUIRED",
                message=f"删除策略配置 {name} 需要 --confirm。",
                blocking=True,
            )
        doc = self.get(name)
        if doc.status == StrategyStatus.ACTIVE:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_DELETE_ACTIVE_FORBIDDEN",
                message=f"策略配置 {name} 处于 active 状态，禁止直接删除。",
                blocking=True,
                suggested_action="先 `configure strategy disable` 停用后再删除。",
            )
        children = [d.profile_name for d in self.list() if d.inheritance_parent == name]
        if children:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_PARENT_IN_USE",
                message=f"策略配置 {name} 仍被以下派生策略继承：{'、'.join(children)}",
                context={"children": children},
                blocking=True,
                suggested_action="先删除或改挂派生策略的 inheritance_parent。",
            )

        from trash import TrashStore

        entry = TrashStore(self.ws).move(
            self._profile_path(name),
            category="strategy_profile",
            deleted_by=deleted_by,
            reason=reason or f"删除策略配置 {name}",
        )
        self._rebuild_meta()
        return entry.bucket_dir

    def _require_parent(self, doc: StrategyProfileDoc) -> None:
        if not self._profile_path(doc.inheritance_parent).exists():
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_PARENT_NOT_FOUND",
                message=(f"策略配置 {doc.profile_name} 的父级 {doc.inheritance_parent} 不存在。"),
                blocking=True,
                suggested_action="先创建父级模板，或修正 inheritance_parent。",
            )

    def _profile_path(self, name: str) -> Path:
        return self.path / f"{name}.yaml"

    def _load_all(self) -> list[StrategyProfileDoc]:
        docs: list[StrategyProfileDoc] = []
        for f in sorted(self.path.glob("*.yaml")):
            if f.name == _META_FILE:
                continue
            docs.append(self._load_doc(f))
        return docs

    def _load_doc(self, f: Path) -> StrategyProfileDoc:
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        try:
            return StrategyProfileDoc.model_validate(raw)
        except ValidationError as exc:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_INVALID_FILE",
                message=f"策略配置文件校验失败：{f.name} — {_format_validation_error(exc)}",
                context={"file": str(f)},
                blocking=True,
                related_files=[str(f)],
                suggested_action="按报错逐条修正字段后重试；枚举可选值见 `configure strategy init` 骨架注释。",
            ) from exc

    def _save_doc(self, doc: StrategyProfileDoc) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self._profile_path(doc.profile_name).write_text(
            yaml.safe_dump(
                doc.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def _rebuild_meta(self) -> None:
        names = sorted(f.stem for f in self.path.glob("*.yaml") if f.name != _META_FILE)
        meta = _MetaDoc(version=1, profiles=names)
        (self.path / _META_FILE).write_text(
            yaml.safe_dump(meta.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

__all__ = ["StrategyProfileStore", "store_path"]
