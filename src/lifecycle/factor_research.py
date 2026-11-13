from __future__ import annotations

import json
from difflib import SequenceMatcher
from math import comb
from typing import Any

import pandas as pd

from exceptions import LifecycleError
from paths import workspace

from .models import FactorVersion, ObjectType
from .store import LifecycleStore

class FactorResearchIndex:

    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    def assess_similarity(self, factor: FactorVersion) -> dict[str, Any]:
        source = self._source(factor)
        dependencies = set(factor.dependencies)
        best: dict[str, Any] | None = None
        for row in self.store.list_objects(ObjectType.FACTOR_VERSION, limit=5000):
            other_id = row["object_id"]
            if other_id == factor.factor_version_id:
                continue
            other = self.store.get(ObjectType.FACTOR_VERSION, other_id)
            assert isinstance(other, FactorVersion)
            other_source = self._source(other)
            code_similarity = SequenceMatcher(None, source, other_source).ratio()
            union = dependencies | set(other.dependencies)
            dependency_jaccard = (
                len(dependencies & set(other.dependencies)) / len(union) if union else 1.0
            )
            score = max(code_similarity, dependency_jaccard)
            candidate = {
                "neighbor_factor_version_id": other_id,
                "definition_similarity": round(score, 6),
                "code_similarity": round(code_similarity, 6),
                "dependency_jaccard": round(dependency_jaccard, 6),
                "exact_code_duplicate": factor.code_hash == other.code_hash,
            }
            if best is None or candidate["definition_similarity"] > best["definition_similarity"]:
                best = candidate
        result = {
            **(best or {}),
            "screen": "definition_and_dependency",
            "needs_signal_correlation": True,
            "duplicate_blocked": bool(
                best
                and (
                    best["exact_code_duplicate"]
                    or (
                        best["code_similarity"] >= 0.995
                        and best["dependency_jaccard"] >= 0.95
                    )
                )
            ),
        }
        if best:
            other = self.store.get(
                ObjectType.FACTOR_VERSION, best["neighbor_factor_version_id"]
            )
            assert isinstance(other, FactorVersion)
            signal = self._signal_similarity(factor, other)
            result.update(signal)
            result["needs_signal_correlation"] = signal.get(
                "signal_correlation_status"
            ) != "completed"
            result["signal_redundancy_candidate"] = bool(
                float(signal.get("median_absolute_rank_correlation") or 0) >= 0.95
            )
        return result

    def multiple_testing(
        self,
        *,
        session_trials: int,
        trial_index: int,
        fold_excess_returns: list[float],
    ) -> dict[str, Any]:
        previous = len(
            self.store.list_objects(ObjectType.FACTOR_EVALUATION, limit=5000)
        )
        total = max(1, previous + 1, session_trials, trial_index)
        fold_count = len(fold_excess_returns)
        positives = sum(value > 0 for value in fold_excess_returns)
        raw_p = (
            sum(comb(fold_count, k) for k in range(positives, fold_count + 1))
            / (2**fold_count)
            if fold_count
            else 1.0
        )
        adjusted = min(1.0, raw_p * total)
        return {
            "global_trial_count_at_evaluation": total,
            "session_trial_count": max(session_trials, trial_index),
            "session_trial_index": trial_index,
            "family_wise_alpha": 0.05,
            "bonferroni_alpha": 0.05 / total,
            "fold_count": fold_count,
            "positive_fold_count": positives,
            "one_sided_sign_test_p_value": raw_p,
            "bonferroni_adjusted_p_value": adjusted,
            "p_value_available": bool(fold_count),
            "correction_status": (
                "passed" if adjusted <= 0.05 else "not_significant_or_insufficient_power"
            ),
        }

    def _signal_similarity(
        self, factor: FactorVersion, other: FactorVersion
    ) -> dict[str, Any]:
        
        from quantlens.research_data import factor_matrix
        from factors.registry import FactorRegistry
        program=self.store.get(ObjectType.RESEARCH_PROGRAM,factor.research_program_id)
        if program.validation_window is None:
            return {'signal_correlation_status':'window_missing','signal_correlation_error':'研究任务缺少验证区间'}
        validation_start=pd.Timestamp(program.validation_window.start)
        validation_end=pd.Timestamp(program.validation_window.end)
        def scores(record):
            name=record.factor_name
            meta=FactorRegistry().get(name)
            return factor_matrix(name,meta.version,validation_start.date().isoformat(),validation_end.date().isoformat())
        try:
            left, right = scores(factor).align(scores(other), join="inner")
        except LifecycleError as exc:
            return {
                "signal_correlation_status": "failed",
                "signal_correlation_error": f"[{exc.code}] {exc.message}",
            }
        correlations = left.corrwith(right, axis=1, method="spearman").dropna()
        if correlations.empty:
            return {
                "signal_correlation_status": "insufficient_overlap",
                "signal_correlation_error": "无有效的同日横截面排名相关系数",
            }
        return {
            "signal_correlation_status": "completed",
            "signal_correlation_neighbor_id": other.factor_version_id,
            "median_rank_correlation": float(correlations.median()),
            "median_absolute_rank_correlation": float(correlations.abs().median()),
            "p95_absolute_rank_correlation": float(correlations.abs().quantile(0.95)),
            "signal_correlation_days": int(len(correlations)),
            "signal_correlation_window_start": validation_start.date().isoformat(),
            "signal_correlation_window_end": validation_end.date().isoformat(),
        }

    @staticmethod
    def incremental_value(
        *, score: float, parent_score: float | None, validation_metrics: dict[str, Any]
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "score": score,
            "parent_score": parent_score if parent_score is not None else "baseline",
            "score_delta": score - parent_score if parent_score is not None else 0.0,
        }
        returns = validation_metrics.get("returns") or {}
        costs = validation_metrics.get("costs") or {}
        result.update(
            {
                "annualized_excess_return": returns.get("annualized_excess_return"),
                "annualized_turnover": costs.get("annualized_turnover"),
                "cost_total": costs.get("total_cost"),
            }
        )
        return result

    @staticmethod
    def _source(factor: FactorVersion) -> str:
        path = workspace().root / factor.code_ref
        if not path.is_file():
            return factor.code_hash
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleError(
                code="FACTOR_VERSION_ARTIFACT_INVALID",
                message=f"无法读取因子版本 artifact：{factor.code_ref}",
                context={"error": str(exc)},
                blocking=True,
                suggested_action="修复因子版本 JSON，不得以哈希或默认代码代替。",
            ) from exc
        source = raw.get("code")
        if not isinstance(source, str) or not source.strip():
            raise LifecycleError(
                code="FACTOR_VERSION_CODE_MISSING",
                message=f"因子版本 artifact 缺少代码：{factor.code_ref}",
                blocking=True,
                suggested_action="恢复原始候选代码后重新登记。",
            )
        return "\n".join(line.strip() for line in source.splitlines() if line.strip())

__all__ = ["FactorResearchIndex"]
