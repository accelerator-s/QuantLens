from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from exceptions import LifecycleError, PanelCacheError
from market_rules import MarketRuleRegistry
from paths import workspace
from session import has_active_market, load_session

from .models import DataSnapshot, ObjectType, QualityStatus
from .store import LifecycleStore

def _canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _cutoff(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time.max, tzinfo=UTC)

class DataSnapshotService:

    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    def create(self, *, require_strict: bool = False) -> DataSnapshot:
        from panel.api import cache_status

        session = load_session()
        if not has_active_market(session):
            raise LifecycleError(
                code="DATA_SNAPSHOT_MARKET_MISSING",
                message="工作区尚未选择活跃市场和品种",
                blocking=True,
                suggested_action="先在 WebUI 管理页选择市场和品种。",
            )
        rule = MarketRuleRegistry().resolve(
            session.active_market, session.active_instrument_class
        )
        statuses = cache_status()
        default = statuses["default"]
        if default.state != "fresh" or default.meta is None:
            raise PanelCacheError(
                code="DATA_SNAPSHOT_DEFAULT_CACHE_NOT_FRESH",
                message=f"default PIT 矩阵缓存状态为 {default.state}，不能创建快照",
                context={"reasons": default.reasons},
                blocking=True,
                suggested_action="先在数据页完成同步和矩阵缓存重建。",
            )
        strict = statuses["strict"]
        if require_strict and (strict.state != "fresh" or strict.meta is None):
            raise PanelCacheError(
                code="DATA_SNAPSHOT_STRICT_CACHE_NOT_FRESH",
                message=f"基本面研究要求 strict 缓存，但当前状态为 {strict.state}",
                context={"reasons": strict.reasons},
                blocking=True,
                suggested_action="先重建 strict PIT 缓存，再启动基本面因子研究。",
            )

        ws = workspace()
        manifest_paths = sorted(path for path in ws.data_manifests.rglob("*") if path.is_file())
        if not manifest_paths:
            raise LifecycleError(
                code="DATA_SNAPSHOT_MANIFEST_MISSING",
                message="没有可审计的数据 manifest，拒绝创建 DataSnapshot",
                blocking=True,
                suggested_action="先通过数据源同步生成 data/manifests 下的 manifest。",
            )

        metas = {"default": default.meta}
        warnings: list[str] = []
        if strict.state == "fresh" and strict.meta is not None:
            metas["strict"] = strict.meta
        else:
            warnings.extend(f"strict:{reason}" for reason in strict.reasons)

        manifest_refs = [str(path.relative_to(ws.root)) for path in manifest_paths]
        fingerprints: dict[str, str] = {
            f"cache:{variant}": _canonical_hash(meta) for variant, meta in metas.items()
        }
        fingerprints.update(
            {
                f"manifest:{path.relative_to(ws.root)}": _file_hash(path)
                for path in manifest_paths
            }
        )

        pit_cutoffs: dict[str, datetime] = {}
        for variant, meta in metas.items():
            for table, source in (meta.get("sources") or {}).items():
                last_date = source.get("manifest_last_date")
                if last_date:
                    pit_cutoffs[f"{variant}:{table}"] = _cutoff(str(last_date))

        params = default.meta.get("build_params") or {}
        end = str(params.get("end") or "")
        if not end:
            raise LifecycleError(
                code="DATA_SNAPSHOT_CUTOFF_MISSING",
                message="default 缓存元数据缺少 build_params.end",
                blocking=True,
                suggested_action="重新构建矩阵缓存以补齐可审计截止日期。",
            )
        axes = default.meta.get("axes") or {}
        semantic = {
            "market": session.active_market,
            "instrument_class": session.active_instrument_class,
            "market_rule": rule.name,
            "cutoff": end,
            "fingerprints": fingerprints,
            "axes": axes,
        }
        snapshot_id = f"DS-{_canonical_hash(semantic)[:20].upper()}"
        try:
            existing = self.store.get(ObjectType.DATA_SNAPSHOT, snapshot_id)
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_REFERENCE_NOT_FOUND":
                raise
        else:
            assert isinstance(existing, DataSnapshot)
            return existing

        parent_snapshot_id = self._latest_snapshot_id(
            session.active_market, session.active_instrument_class
        )
        snapshot = DataSnapshot(
            snapshot_id=snapshot_id,
            as_of=_cutoff(end),
            market=session.active_market,
            instrument_class=session.active_instrument_class,
            manifest_refs=manifest_refs,
            dataset_fingerprints=fingerprints,
            pit_cutoffs=pit_cutoffs,
            universe_ref=(
                f"matrix_cache:{session.active_market}:{session.active_instrument_class}:"
                f"{axes.get('dates_hash', '')}:{axes.get('instruments_hash', '')}"
            ),
            market_rule_ref=rule.name,
            quality_status=QualityStatus.WARNING if warnings else QualityStatus.PASSED,
            quality_event_refs=warnings,
            parent_snapshot_id=parent_snapshot_id,
        )
        self.store.add(snapshot)
        return snapshot

    def _latest_snapshot_id(self, market: str, instrument_class: str) -> str | None:
        for row in self.store.list_objects(ObjectType.DATA_SNAPSHOT, limit=5000):
            record = self.store.get(ObjectType.DATA_SNAPSHOT, row["object_id"])
            if record.market == market and record.instrument_class == instrument_class:
                return record.snapshot_id
        return None

__all__ = ["DataSnapshotService"]
