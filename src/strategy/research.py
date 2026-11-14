"""Account-bound strategy fitting, frozen evaluation and research reporting."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from exceptions import ContractViolation
from paths import workspace
from security import confined_path
from strategy import StrategyProfileDoc, StrategyProfileStore, StrategyStatus

from strategy.training import METHODS
MODELS = [{"name": "auto", "display_name": "自动研究", "description": "AI 根据训练证据选择候选，本地算法拟合权重"}] + [
    {"name": name, "display_name": description, "description": description} for name, description in METHODS.items()]
from portfolio.transaction import lock as _lock


class ResearchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=80)
    factors: list[str] = Field(min_length=2, max_length=30)
    account_id: str = Field(min_length=1, max_length=128)
    model: Literal['auto', 'equal', 'icir', 'model_linear', 'model_ridge', 'model_logistic'] = 'auto'
    train_start: date
    train_end: date
    start: date
    end: date
    top_n: int = Field(default=20, ge=1, le=100)
    rebalance_frequency: Literal['weekly', 'monthly', 'quarterly'] = 'monthly'
    label_horizon: Literal[1, 5, 10, 20] = 20
    max_candidates: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def check_windows(self):
        if not self.train_start < self.train_end < self.start < self.end:
            raise ValueError("日期须满足：训练开始 < 训练结束 < 回测开始 < 回测结束。")
        if len(set(self.factors)) != len(self.factors):
            raise ValueError("因子不能重复。")
        if not self.display_name.strip():
            raise ValueError("请填写策略名称。")
        return self


def result_path(name: str) -> Path:
    root = Path("/data/ads") if os.environ.get("SPARK_HOME") and Path("/data/ads").is_dir() else workspace().data / "ads"
    return confined_path(root / "_strategy_research", name, ".json")


def read_result(name: str) -> dict | None:
    path = result_path(name)
    return json.loads(path.read_text()) if path.exists() else None


def write_result(name: str, result: dict) -> None:
    path = result_path(name)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            owner = path.parent.parent.stat()
            os.chown(path.parent, owner.st_uid, owner.st_gid)
        pending = path.with_suffix(".pending")
        pending.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, default=str))
        if os.geteuid() == 0:
            owner = path.parent.stat()
            os.chown(pending, owner.st_uid, owner.st_gid)
        pending.replace(path)


def is_research(doc) -> bool:
    return "research_spec" in doc.extra and not doc.is_template and not doc.extra.get("internal_only")


def require_strategy(name: str):
    doc = StrategyProfileStore().get(name)
    if not is_research(doc):
        raise ContractViolation(code="STRATEGY_NOT_FOUND", message="策略不存在。")
    return doc


def defaults() -> dict:
    from quantlens import ads
    coverage = ads.query("overview", limit=1)
    if not coverage:
        return {}
    row = coverage[0]
    first, last = date.fromisoformat(str(row["start"])[:10]), date.fromisoformat(str(row["end"])[:10])
    start = max(first + timedelta(days=730), last - timedelta(days=365))
    return {"train_start": str(max(first, start - timedelta(days=1095))), "train_end": str(start - timedelta(days=10)), "start": str(start), "end": str(last)}


def create_strategy(spec: ResearchSpec, *, hypothesis: str = "", discussion_model: str = ""):
    from factors.registry import FactorRegistry, FactorStatus
    from backtest.protocol import verify_protocol_or_raise
    from strategy.account_binding import account_snapshot
    snapshot = account_snapshot(spec.account_id)
    registry = FactorRegistry()
    metadata = {}
    for name in spec.factors:
        factor = registry.get(name)
        metadata[name] = factor
        if factor.status not in {FactorStatus.ACTIVE, FactorStatus.DRAFT} or factor.direction not in {"positive", "negative"}:
            raise ContractViolation(code="STRATEGY_FACTOR_UNAVAILABLE", message=f"因子 {name} 必须已启用或处于草稿研究状态，且方向明确。")
    protocol = verify_protocol_or_raise()
    name = "strategy_" + uuid.uuid4().hex[:12]
    doc = StrategyProfileDoc.model_validate({
        "profile_name": name, "display_name": spec.display_name.strip(), "status": "draft",
        "market": "CN-A", "instruments": ["stock"], "timescale": "daily", "data_frequency": "daily",
        "universe": {"min_listed_days": 120, "exclude_suspended": True, "exclude_st": True},
        "factors": {"weighting": 'equal', "items": [{"name": f, "direction_override": metadata[f].direction} for f in spec.factors]},
        "standardization_chain": [{"step": "zscore"}],
        "trading_rules": {"market_rule": protocol.market_rule_name},
        "execution_assumption": {"price_anchor": protocol.price_anchor, "fill_assumption": protocol.fill_assumption},
        "rebalance_frequency": spec.rebalance_frequency, "holding_period": 5,
        "position_constraint": {"top_n": spec.top_n, "max_weight_per_name": .1, "min_cash_ratio": .1},
        "fees_slippage": {"fee_source": protocol.fee_source, "slippage_bps": protocol.slippage_bps},
        "risk_control": {"max_single_name_pct": .1, "max_industry_pct": .3}, "price_deviation_threshold": .02,
        "constraints": {"max_positions": spec.top_n}, "benchmark": protocol.benchmark,
        "extra": {"research_hypothesis": hypothesis, "discussion_model": discussion_model, "research_spec": spec.model_dump(mode="json"),
                  "account_binding": {"account_id": spec.account_id}, "creation_account_snapshot": snapshot,
                  "factor_versions": {f: metadata[f].version for f in spec.factors}},
    })
    StrategyProfileStore().upsert(doc)
    from strategy.capital_guard import check_account
    check_account(spec.account_id, reason='strategy_created')
    return doc


def run_research(name: str, report, *, analyst=None, evaluator=None) -> dict:
    from backtest.config import BacktestConfig
    from backtest.protocol import verify_protocol_or_raise
    from quantlens.compute import submit
    from quantlens.sync_ads import sync
    from quantlens.backtest_publication import publication_lock
    from strategy.account_binding import account_snapshot, require_binding
    from strategy.training import Candidate, search, configured_responder, beats
    from factor_evolution.diagnostics import digest
    from strategy.capital_guard import check_account, assert_generation
    doc = require_strategy(name)
    spec = ResearchSpec.model_validate(doc.extra["research_spec"])
    profile = StrategyProfileStore().resolve(name)
    require_binding(profile, spec.account_id)
    snapshot = account_snapshot(spec.account_id)
    generation = check_account(spec.account_id, reason='research_start')['generation']
    profile = profile.model_copy(update={'extra': {**profile.extra, 'research_account_snapshot': snapshot}})
    protocol = verify_protocol_or_raise()
    config = BacktestConfig(profile_name=name, start=str(spec.start), end=str(spec.end),
                            initial_capital=snapshot['capital'], account_id=spec.account_id,
                            account_name=snapshot['alias'], star_market_enabled=snapshot['star_market_enabled'])
    from benchmarks.selection import freeze_profile
    profile = freeze_profile(profile, str(spec.train_start), str(spec.end), key=config.content_hash(),
                             model_name=doc.extra.get('discussion_model') or None)
    run_id = "BT-" + uuid.uuid4().hex
    batch = "strategy-" + uuid.uuid4().hex
    write_result(name, {"state": "running", "run_id": run_id, "spec": spec.model_dump(mode="json")})
    try:
        responder = analyst or configured_responder(doc.extra.get('discussion_model') or None)
        mandate = {'research_id': batch, 'account': snapshot, 'spec': spec.model_dump(mode='json'),
                   'benchmark_plan': profile.extra['benchmark_plan'], 'factor_versions': profile.extra['factor_versions'],
                   'risk_constraints': profile.constraints.model_dump(mode='json')}
        def evaluate(candidate, stage='inner'):
            assert_generation(spec.account_id, generation)
            ident = 'BT-'+uuid.uuid4().hex if stage == 'inner' else run_id if candidate == final_candidate else 'BT-'+uuid.uuid4().hex
            if evaluator is not None:
                result = evaluator(candidate=candidate, stage=stage, profile=profile, spec=spec,
                    protocol=protocol, config=config, run_id=ident, batch=batch)
                if stage == 'final':
                    from quantlens.backtest_publication import publish_completed_runs
                    publish_completed_runs([fold['run_id'] for fold in result['folds']], reporter=report)
                return result
            with publication_lock():
                submit("spark_strategy_research", "--profile", profile.model_dump_json(),
                   "--spec", spec.model_dump_json(), "--protocol", protocol.model_dump_json(),
                   "--config", config.model_dump_json(), "--run-id", ident, "--batch", batch,
                   '--stage', stage, '--candidate', candidate.model_dump_json(), reporter=report)
                result = read_result(name)
                if not result or result.get('state') != 'computed' or result.get('run_id') != ident or result.get('stage') != stage:
                    raise ContractViolation(code='STRATEGY_RESULT_MISSING', message='策略阶段结果未完整写入。')
                if stage == 'final':
                    sync(batch)
                return result['evaluation']
        frozen = search(spec, mandate, evaluate, responder, report)
        final_candidate = Candidate.model_validate(frozen['candidate'])
        directory = result_path(name).parent / 'studies' / batch
        directory.mkdir(parents=True, exist_ok=False)
        (directory/'frozen.json').write_text(json.dumps(frozen, ensure_ascii=False, allow_nan=False))
        report('候选及全部研究约束已冻结，执行最终评估；此后不再调参')
        baseline_candidate = Candidate.model_validate(frozen['results'][0]['candidate'])
        final_baseline = evaluate(baseline_candidate, 'final') if frozen['winner'] else None
        final = evaluate(final_candidate, 'final')
        for item in [final, *([final_baseline] if final_baseline else [])]:
            for key in ['data_snapshot', 'pipeline_code_hash']:
                if item[key] != frozen['results'][0][key]:
                    raise ValueError('最终评估的数据或计算代码与内层研究不一致')
        fold = final['folds'][0]
        accepted = (fold['metrics']['returns']['annualized_return'] > 0 and
                    fold['metrics']['objective']['eligible'] and
                    (final_baseline is None or beats(final, final_baseline)))
        result = {'state': 'completed', 'run_id': run_id, 'spec': spec.model_dump(mode='json'),
                  'profile_name': name, 'research_model': getattr(responder, 'model_name', ''),
                  'completed_at': datetime.now(UTC).isoformat(),
                  'account_snapshot': snapshot, 'selected_candidate': frozen['candidate'],
                  'accepted': bool(accepted), 'freeze_hash': frozen['freeze_hash'], 'search': frozen,
                  'final_evaluation': final, 'final_baseline': final_baseline,
                  'weights': fold['weights'], 'factor_versions': profile.extra['factor_versions'],
                  'metrics': fold['metrics'], 'training': fold['training'],
                  'report': '\n'.join([f'# {doc.display_name}', '',
                      f'关联账户：{snapshot["alias"]}；本次研究资金 {snapshot["capital"]} 元。',
                      f'方法：{final_candidate.method}；持仓上限 {final_candidate.max_positions}；调仓 {final_candidate.frequency}。',
                      '训练内分段诊断后冻结方案，最终评估结果不再用于本轮调参。历史上已经查看的区间不能视为全新样本外。',
                      f'最终年化净收益 {fold["metrics"]["returns"]["annualized_return"]:.2%}；最大回撤 {fold["metrics"]["risk"]["max_drawdown"]:.2%}。',
                      '通过启用门槛。' if accepted else '未通过启用门槛，保留研究结果，不发布可用策略。'])}
        from lifecycle.experiment_summary import generate_summary
        try:
            result['experiment_summary'] = generate_summary({
                'kind': 'strategy', 'state': 'completed', 'training_completed': True,
                'candidate_beats_training_baseline': bool(frozen['winner']),
                'final_evaluation_completed': True, 'accepted': bool(accepted),
                'selected_method': final_candidate.method,
            }, model_name=doc.extra.get('discussion_model') or None)
        except Exception as exc:
            result['experiment_summary_error'] = str(exc)
            report('实验总结生成失败，计算结果已保留')
        (directory/'result.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, default=str))
        write_result(name, result)
        # An explicit stop made while computing always wins over publication.
        with _lock:
            assert_generation(spec.account_id, generation)
            store = StrategyProfileStore()
            current = store.get(name)
            if accepted:
                from strategy import ResolvedStrategyProfile
                fitted = ResolvedStrategyProfile.model_validate(final['profile'])
                current.factors = fitted.factors
                current.constraints = fitted.constraints
                current.position_constraint = fitted.position_constraint
                current.rebalance_frequency = fitted.rebalance_frequency
                current.extra = {**fitted.extra, **{k: v for k, v in current.extra.items()
                                 if k in {'capital_suspension', 'capital_manual_enable'}}}
            current.updated_at = datetime.now(UTC)
            store._save_doc(current)
        return result
    except Exception as exc:
        write_result(name, {"state": "failed", "run_id": run_id, "error": str(exc), "spec": spec.model_dump(mode="json")})
        raise


def start_research(name: str) -> dict:
    from webui.backend.tasks import manager
    with _lock:
        doc = require_strategy(name)
        if doc.status == StrategyStatus.ACTIVE:
            raise ContractViolation(code="STRATEGY_ACTIVE", message="请先停止策略，再重新研究。")
        if manager.latest(resource=f"strategy-research:{name}", running_only=True):
            raise ContractViolation(code="STRATEGY_BUSY", message="该策略正在研究中。")
        doc.status = StrategyStatus.DRAFT
        doc.updated_at = datetime.now(UTC)
        StrategyProfileStore()._save_doc(doc)
        task_id = manager.start(f"strategy-research:{name}", lambda report: run_research(name, report),
                                resource=f"strategy-research:{name}", category="research")
        return {"task_id": task_id, "profile_name": name}


def set_status(name: str, target: StrategyStatus):
    from webui.backend.tasks import manager
    with _lock:
        doc = require_strategy(name)
        if target == StrategyStatus.ACTIVE:
            from strategy.capital_guard import check_account
            guard = check_account(doc.extra['account_binding']['account_id'], reason='manual_enable')
            doc = require_strategy(name)
            if guard.get('band') == 'unavailable':
                raise ContractViolation(code='STRATEGY_ACCOUNT_VALUATION', message='账户估值不可用，修复数据后才能启用。')
            from factors.registry import FactorRegistry, FactorStatus
            for ref in doc.factors.items:
                if FactorRegistry().get(ref.name).status != FactorStatus.ACTIVE:
                    raise ContractViolation(code="STRATEGY_FACTOR_UNAVAILABLE", message=f"因子 {ref.name} 已停止，请先启用因子。")
            result = read_result(name)
            if manager.latest(resource=f"strategy-research:{name}", running_only=True) or not result or result.get("state") != "completed" or not result.get('accepted'):
                raise ContractViolation(code="STRATEGY_RESULT_REQUIRED", message="研究完成后才能启用策略。")
            doc.extra.pop('capital_suspension', None)
            doc.extra['capital_manual_enable'] = {'at': datetime.now(UTC).isoformat(),
                'generation': guard['generation'], 'band': guard['band'], 'capital': guard['snapshot']['capital']}
        doc.status = target
        doc.updated_at = datetime.now(UTC)
        StrategyProfileStore()._save_doc(doc)
        return doc
