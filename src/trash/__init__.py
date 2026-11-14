from __future__ import annotations

import secrets
import shutil
import threading
from dataclasses import dataclass
from functools import wraps
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from exceptions import QuantTrainingError
from paths import Workspace, workspace

VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        "factor",
        "strategy_profile",
        "execution_template",
        "market_rule_candidate",
        "config_candidate",
        "data_mapping_candidate",
        "portfolio_account",
    }
)

_lock = threading.RLock()

def synchronized(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        with _lock:
            return method(*args, **kwargs)
    return wrapped

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

@dataclass
class TrashEntry:

    category: str
    trashed_at: datetime
    original_path: str
    deleted_by: str
    reason: str
    bucket_dir: Path

    @property
    def content_path(self) -> Path:
        return self.bucket_dir / "content"

    @property
    def meta_path(self) -> Path:
        return self.bucket_dir / "meta.yaml"

class TrashStore:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    @synchronized
    def move(
        self,
        source: Path,
        *,
        category: str,
        deleted_by: str,
        reason: str = "",
        name: str = "",
    ) -> TrashEntry:
        if category not in VALID_CATEGORIES:
            raise QuantTrainingError(
                code="TRASH_CATEGORY_INVALID",
                message=f"非法 trash category：{category}",
                context={"valid": sorted(VALID_CATEGORIES)},
                blocking=True,
            )
        if not source.exists():
            raise QuantTrainingError(
                code="TRASH_SOURCE_MISSING",
                message=f"待回收对象不存在：{source}",
                blocking=True,
            )
        if not deleted_by.strip():
            raise QuantTrainingError(
                code="TRASH_DELETED_BY_REQUIRED",
                message="移入回收站需指定 deleted_by。",
                blocking=True,
            )

        ts = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        bucket = self.ws.trash / category / f"{ts}__{source.name}__{secrets.token_hex(2)}"
        bucket.mkdir(parents=True, exist_ok=False)
        target = bucket / "content"
        shutil.move(str(source), str(target))

        meta: dict[str, Any] = {
            "category": category,
            "trashed_at": _utcnow().isoformat(),
            "original_path": str(source.resolve()),
            "deleted_by": deleted_by.strip(),
            "reason": reason,
            "name": name or source.stem,
        }
        (bucket / "meta.yaml").write_text(
            yaml.safe_dump(meta, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self._read_entry(bucket)

    @synchronized
    def remove_factor(self, name: str, *, deleted_by: str = "webui", reason: str = "") -> TrashEntry:
        from factors.registry import FactorRegistry

        registry = FactorRegistry(self.ws.config / "factors.yaml")
        factor = registry.get(name)
        source = self.ws.root / (factor.plugin_path or f"factors/{name}.py")
        # Built-in implementations and shared plugin files are not owned by one factor.
        source_retained = not source.resolve().is_relative_to((self.ws.root / "factors").resolve()) or any(
            other.name != name
            and (self.ws.root / (other.plugin_path or f"factors/{other.name}.py")).resolve() == source.resolve()
            for other in registry.load().factors
        )
        bucket = self.ws.trash / "factor" / f"{_utcnow():%Y%m%dT%H%M%SZ}__{name}.py__{secrets.token_hex(4)}"
        bucket.mkdir(parents=True, exist_ok=False)
        meta = {
            "category": "factor", "trashed_at": _utcnow().isoformat(),
            "original_path": str(source.resolve()), "deleted_by": deleted_by or "webui",
            "reason": reason, "factor_metadata": factor.model_dump(mode="json"),
            "name": factor.display_name or factor.name,
            "source_retained": source_retained,
        }
        # Save metadata even for factors that have no source file.
        try:
            (bucket / "meta.yaml").write_text(
                yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8",
            )
            if source.exists():
                if source_retained:
                    shutil.copy2(source, bucket / "content")
                else:
                    shutil.move(str(source), str(bucket / "content"))
            registry.remove(name, confirm=True)
        except Exception:
            if not source_retained and (bucket / "content").exists():
                shutil.move(str(bucket / "content"), str(source))
            shutil.rmtree(bucket)
            raise
        return self._read_entry(bucket)

    @synchronized
    def list(self, *, category: str | None = None) -> list[TrashEntry]:
        if category and category not in VALID_CATEGORIES:
            raise QuantTrainingError(code="TRASH_CATEGORY_INVALID", message="回收站类别无效。", blocking=True)
        if not self.ws.trash.exists():
            return []
        out: list[TrashEntry] = []
        roots = (
            [self.ws.trash / category]
            if category
            else [p for p in self.ws.trash.iterdir() if p.is_dir()]
        )
        for root in roots:
            if not root.exists() or root.is_symlink() or root.name not in VALID_CATEGORIES:
                continue
            for bucket in sorted(root.iterdir()):
                if bucket.is_dir() and not bucket.is_symlink() and (bucket / "meta.yaml").exists():
                    out.append(self._read_entry(bucket))
        out.sort(key=lambda e: e.trashed_at, reverse=True)
        return out

    @synchronized
    def restore(
        self,
        bucket_name: str,
        *,
        target: Path | None = None,
        confirm: bool = False,
    ) -> Path:
        if not confirm:
            raise QuantTrainingError(
                code="TRASH_RESTORE_CONFIRM_REQUIRED",
                message="restore 需要 --confirm。",
                blocking=True,
            )
        bucket = self._find_bucket(bucket_name)
        entry = self._read_entry(bucket)
        dest = target or Path(entry.original_path)
        resolved = dest.resolve()
        root = self.ws.root.resolve()
        if dest.is_symlink() or resolved == root or not resolved.is_relative_to(root) or resolved.is_relative_to(self.ws.trash.resolve()):
            raise QuantTrainingError(code="TRASH_UNSAFE_TARGET", message="恢复路径超出允许目录。", blocking=True)
        meta = yaml.safe_load(entry.meta_path.read_text(encoding="utf-8")) or {}
        factor = None
        registry = None
        if entry.category == "factor" and meta.get("factor_metadata"):
            from factors.registry import FactorMetadata, FactorRegistry
            factor = FactorMetadata.model_validate(meta["factor_metadata"])
            registry = FactorRegistry(self.ws.config / "factors.yaml")
            if any(item.name == factor.name for item in registry.load().factors):
                raise QuantTrainingError(
                    code="FACTOR_EXISTS", message=f"因子 {factor.name} 已存在。", blocking=True,
                )
        keep_existing_source = (
            factor is not None and meta.get("source_retained") and target is None and dest.exists()
        )
        if dest.exists() and not keep_existing_source:
            raise QuantTrainingError(
                code="TRASH_RESTORE_TARGET_EXISTS",
                message=f"恢复目标已存在：{dest}",
                blocking=True,
                suggested_action="先确认是否真的要覆盖；如要覆盖，请手动移走再 restore。",
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        has_content = entry.content_path.exists() and not keep_existing_source
        if has_content:
            shutil.move(str(entry.content_path), str(dest))
        try:
            if factor is not None:
                if target is not None:
                    factor.plugin_path = str(dest.relative_to(self.ws.root))
                # Restore the original metadata, including timestamps and permissions.
                doc = registry.load()
                doc.factors.append(factor)
                registry._save(doc)
            elif entry.category == "strategy_profile":
                from strategy.store import StrategyProfileStore
                StrategyProfileStore(ws=self.ws)._rebuild_meta()
        except Exception:
            if has_content:
                shutil.move(str(dest), str(entry.content_path))
            raise
        shutil.rmtree(bucket)
        return dest

    @synchronized
    def purge(
        self,
        bucket_name: str,
        *,
        confirm: bool = False,
    ) -> None:
        if not confirm:
            raise QuantTrainingError(
                code="TRASH_PURGE_CONFIRM_REQUIRED",
                message="purge 需要 --confirm。",
                blocking=True,
            )
        bucket = self._find_bucket(bucket_name)
        shutil.rmtree(bucket)

    @synchronized
    def empty(self, *, confirm: bool = False) -> int:
        if not confirm:
            raise QuantTrainingError(
                code="TRASH_PURGE_CONFIRM_REQUIRED", message="清空回收站需要 confirm。", blocking=True,
            )
        entries = self.list()
        for entry in entries:
            shutil.rmtree(entry.bucket_dir)
        return len(entries)

    @synchronized
    def purge_older_than(
        self,
        days: int,
        *,
        confirm: bool = False,
    ) -> list[str]:
        
        if not confirm:
            raise QuantTrainingError(
                code="TRASH_PURGE_CONFIRM_REQUIRED",
                message="周期清理也需要 --confirm。",
                blocking=True,
            )
        cutoff = _utcnow() - timedelta(days=days)
        cleaned: list[str] = []
        for entry in self.list():
            if entry.trashed_at < cutoff:
                shutil.rmtree(entry.bucket_dir)
                cleaned.append(entry.bucket_dir.name)
        return cleaned

    def _find_bucket(self, bucket_name: str) -> Path:
        for entry in self.list():
            if entry.bucket_dir.name == bucket_name:
                return entry.bucket_dir
        raise QuantTrainingError(
            code="TRASH_BUCKET_NOT_FOUND",
            message=f"trash bucket 不存在：{bucket_name}",
            blocking=True,
        )

    @staticmethod
    def _read_entry(bucket: Path) -> TrashEntry:
        meta = yaml.safe_load((bucket / "meta.yaml").read_text(encoding="utf-8")) or {}
        return TrashEntry(
            category=meta.get("category", ""),
            trashed_at=datetime.fromisoformat(meta["trashed_at"]),
            original_path=meta.get("original_path", ""),
            deleted_by=meta.get("deleted_by", ""),
            reason=meta.get("reason", ""),
            bucket_dir=bucket,
        )

__all__ = ["TrashEntry", "TrashStore", "VALID_CATEGORIES"]
