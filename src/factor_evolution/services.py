from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Any

import pandas as pd

from ai.schemas import FactorCandidate
from exceptions import ContractViolation, QuantTrainingError

from .models import (
    EvolutionConfig,
    FactorVersion,
    ResearchWindow,
    ReviewResult,
    VersionResult,
    WindowResult,
)

_CHANGE_TYPES = {
    "formula",
    "lookback",
    "winsorization",
    "neutralization",
    "missing",
    "direction",
    "market_regime",
    "orthogonalization",
}

_AI_CONTRACT_ATTEMPTS = 3

_SYSTEM_PROPOSE = """你是单因子研究助手。输出 FactorCandidate 对象，基于给定父版本只改一个维度。
JSON 根对象不得使用 factor_candidate 等包装键，也不得输出 version_id、parent_version_id、
change_type、change_summary、expected_impact、proposal_id 等 schema 外字段。
根对象必须完整包含 factor_name、display_name、assumption、direction、timescale、
dependencies、parameters、standardization、missing_handling、code、evaluation_metric_hints；
即使字段与父版本相同也必须照抄，禁止省略 factor_name 或 assumption。
display_name 必须是简洁、可解释的自然中文因子名称，不得使用测试编号或英文缩写堆砌。
direction 只能是 long_only_positive、long_only_negative、long_short、undecided。
允许的 change_type：formula、lookback、winsorization、neutralization、missing、direction、market_regime、orthogonalization。
parameters 必须包含三个研究字段：
- _evolution_change_type：上述一个值；
- _change_summary：明确说明改了什么；
- _expected_impact：为什么可能改善。
研究假设必须以可观察的金融现象为起点，不得只给出算子组合。assumption 和
_expected_impact 必须合计说清：现象是什么、从投资者行为/风险补偿/制度约束/
信息扩散到未来收益的传导链、可证伪的预测、为何可能持续，以及在什么市场状态下会失效。
若无法说清传导机制，应停止候选，不得为了改善历史指标堆叠价量算子或追加参数。
standardization 使用完整处理链，允许值：winsorize:下分位:上分位、zscore、rank:average、
industry_neutral:分类ID、size_neutral:mktcap_float、fillna:drop|zero|cross_median|industry_median。
代码提供 compute(panel) 入口，不得使用未来数据。不要改数据区间、收益标签、交易规则、
费用、滑点、样本划分和验收阈值。factor_name 必须保持不变。"""

_SYSTEM_PROPOSE += "\northogonalization 只允许改代码/必要依赖，用 PIT 横截面残差化降低与已有风格的相关性；不得读取其他因子的未登记产物。"

_SYSTEM_REVIEW = """你是单因子实验复盘助手。输出 StructuredConfig 根对象，不得使用
structured_config 包装键，也不得输出 schema 外字段。
config_kind 固定为 factor_evolution_review。proposed_value 必须包含：
- decision：continue 或 stop；
- assessment：只依据给定指标作出的诊断；
- failure_reason：未改善或未达约束的主因，改善时可为空；
- next_change：继续时建议下一轮只改哪一个维度，停止时可为空。
根对象只能包含 config_kind、proposed_value、open_questions，例如：
{"config_kind":"factor_evolution_review","proposed_value":{"decision":"continue","assessment":"...","failure_reason":"...","next_change":"..."},"open_questions":[]}
version_id、experiment_id、change_summary、phenomenon_verification、
market_condition_applicability 都不是根对象字段，严禁输出。
复盘必须用通俗语言回答：证据是否支持原金融现象；若不支持，传导链哪一环可能失效；
结论适用于什么市场状态，又应被什么现象证伪。不得改写回测数值，不得建议放宽固定协议、
样本切分或风控约束，不得承诺未来收益。"""

def _positive_direction(direction: str) -> str:
    mapping = {
        "long_only_positive": "positive",
        "long_only_negative": "negative",
        "long_short": "undecided",
        "undecided": "undecided",
    }
    return mapping.get(direction, direction)

def _candidate_direction(direction: str) -> str:
    mapping = {
        "positive": "long_only_positive",
        "negative": "long_only_negative",
        "undecided": "undecided",
    }
    return mapping.get(direction, direction)

def _candidate_parent(version: FactorVersion) -> dict[str, Any]:
    
    return {
        "factor_name": version.factor_name,
        "display_name": "",
        "assumption": version.assumption,
        "direction": _candidate_direction(version.direction),
        "timescale": version.timescale,
        "dependencies": version.dependencies,
        "parameters": version.parameters,
        "standardization": version.standardization,
        "missing_handling": version.missing_handling,
        "code": version.code,
    }

def _chain_to_tokens(chain: list[Any]) -> list[str]:
    tokens: list[str] = []
    for step in chain:
        raw = step.model_dump(mode="json")
        kind = raw["step"]
        if kind == "winsorize":
            tokens.append(f"winsorize:{raw['lower_pct']}:{raw['upper_pct']}")
        elif kind == "rank":
            tokens.append(f"rank:{raw['method']}")
        elif kind == "industry_neutral":
            tokens.append(f"industry_neutral:{raw['industry_source']}")
        elif kind == "size_neutral":
            tokens.append(f"size_neutral:{raw['size_field']}")
        elif kind == "fillna":
            tokens.append(f"fillna:{raw['method']}")
        else:
            tokens.append(kind)
    return tokens

def _tokens_to_chain(tokens: list[str]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    for token in tokens:
        parts = token.split(":")
        kind = parts[0]
        if kind == "winsorize" and len(parts) == 3:
            chain.append({"step": kind, "lower_pct": parts[1], "upper_pct": parts[2]})
        elif kind == "zscore" and len(parts) == 1:
            chain.append({"step": kind})
        elif kind == "rank" and len(parts) == 2:
            chain.append({"step": kind, "method": parts[1]})
        elif kind == "industry_neutral" and len(parts) == 2:
            chain.append({"step": kind, "industry_source": parts[1]})
        elif kind == "size_neutral" and len(parts) == 2:
            chain.append({"step": kind, "size_field": parts[1]})
        elif kind == "fillna" and len(parts) == 2:
            chain.append({"step": kind, "method": parts[1]})
        else:
            raise ContractViolation(
                code="EVOLUTION_STANDARDIZATION_INVALID",
                message=f"无法识别标准化步骤：{token}",
                context={"token": token},
                blocking=False,
                suggested_action="下一轮按提示中的标准化处理链格式生成候选。",
            )
    return chain

class _LogConsole:
    _markup = re.compile(r"\[[^]]+\]")

    def __init__(self, emit) -> None:
        self.emit = emit

    def print(self, *objects, **_kwargs) -> None:
        text = " ".join(str(obj) for obj in objects)
        self.emit(self._markup.sub("", text))

class ProductionEvolutionServices:
    def __init__(self) -> None:
        self._profiles: dict[str, Any] = {}
        self._published_candidates: dict[str, str] = {}

    def _context(self, config: EvolutionConfig):
        from backtest.config import BacktestConfig, resolve_and_check
        from backtest.protocol import verify_protocol_or_raise

        protocol = verify_protocol_or_raise()
        if not protocol.lock.locked:
            raise QuantTrainingError(
                code="EVOLUTION_PROTOCOL_NOT_LOCKED",
                message="自动进化要求回测协议已锁定。",
                blocking=True,
                suggested_action="先运行 `backtest protocol lock --confirm`，再启动自动进化。",
            )
        key = config.content_hash()
        if key not in self._profiles:
            bt = BacktestConfig(
                profile_name=config.profile_name,
                start=config.train_window.start,
                end=config.train_window.end,
                initial_capital=Decimal(str(config.initial_capital)),
                lookback_buffer_days=config.lookback_buffer_days,
            )
            profile = resolve_and_check(bt, protocol)
            names = [item.name for item in profile.factors.items]
            if names != [config.base_factor]:
                raise QuantTrainingError(
                    code="EVOLUTION_PROFILE_NOT_SINGLE_FACTOR",
                    message=(
                        f"策略 {config.profile_name} 的因子清单为 {names}；第一版自动进化只允许"
                        f"单因子且必须为 {config.base_factor}。"
                    ),
                    blocking=True,
                    suggested_action="选择只引用该基础因子的 ACTIVE 策略 profile。",
                )
            self._profiles[key] = profile
        return protocol, self._profiles[key]

    def load_base(self, config: EvolutionConfig) -> FactorVersion:
        from factors.plugins import FactorPlugin
        from factors.registry import FactorRegistry

        _, profile = self._context(config)
        meta = FactorRegistry().get(config.base_factor)
        source = FactorPlugin.default().read(config.base_factor)
        return FactorVersion(
            version_id="v000-baseline",
            factor_name=config.base_factor,
            assumption=meta.assumption,
            direction=meta.direction,
            timescale=meta.timescale,
            dependencies=list(meta.dependencies),
            parameters=dict(meta.parameters),
            standardization=_chain_to_tokens(profile.standardization_chain),
            missing_handling=meta.missing_handling,
            code=source,
            change_type="baseline",
            change_summary="基础版本",
        )

    def propose(
        self,
        config: EvolutionConfig,
        current: FactorVersion,
        memory: list[dict],
        *,
        iteration: int,
        timeout_seconds: float,
    ) -> FactorVersion:
        from ai.router import AIRouter
        from proposals import ProposalSource, ProposalStore

        prompt = json.dumps(
            {
                "research": {
                    "base_factor": config.base_factor,
                    "iteration": iteration,
                    "random_seed": config.random_seed,
                    "fixed_windows": {
                        "train": config.train_window.text(),
                        "validation": config.validation_window.text(),
                        "locked_test": "hidden_until_final_version",
                    },
                    "walk_forward": config.walk_forward.model_dump(mode="json"),
                    "targets": config.targets.model_dump(mode="json"),
                },
                "parent_version": _candidate_parent(current),
                "session_memory": memory[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        router = AIRouter(request_timeout=max(1.0, min(180.0, timeout_seconds)))
        selected = router.pick("factor_candidate", model_name=config.model_name or None)
        base_prompt = prompt
        last_error: ContractViolation | None = None
        candidate: FactorCandidate | None = None
        payload: dict[str, Any] = {}
        change_type = ""
        change_summary = ""
        expected = ""
        attempts = 0
        for attempts in range(1, _AI_CONTRACT_ATTEMPTS + 1):
            try:
                payload = router.run_task(
                    task="factor_candidate",
                    prompt=prompt,
                    system=_SYSTEM_PROPOSE,
                    model_name=selected.name,
                )
                candidate = FactorCandidate.model_validate(payload)
                if candidate.factor_name != config.base_factor:
                    raise ContractViolation(
                        code="EVOLUTION_FACTOR_NAME_CHANGED",
                        message=f"候选把因子名改成了 {candidate.factor_name}。",
                        blocking=False,
                    )
                change_type = str(candidate.parameters.get("_evolution_change_type") or "")
                change_summary = str(candidate.parameters.get("_change_summary") or "").strip()
                expected = str(candidate.parameters.get("_expected_impact") or "").strip()
                if change_type not in _CHANGE_TYPES or not change_summary:
                    raise ContractViolation(
                        code="EVOLUTION_CHANGE_METADATA_INVALID",
                        message="候选未声明合法的单一改动类型或改动摘要。",
                        context={"change_type": change_type},
                        blocking=False,
                    )
                self._require_single_change(current, candidate, change_type)
                break
            except ContractViolation as exc:
                last_error = exc
                candidate = None
                if attempts >= _AI_CONTRACT_ATTEMPTS:
                    break
                prompt = (
                    f"{base_prompt}\n\n"
                    f"上一次输出未通过候选契约：[{exc.code}] "
                    f"{exc.message[:2500]}\n"
                    "这是格式或单一改动约束问题，不是新的研究轮次。"
                    "保持同一金融假设和父版本，补齐所有 FactorCandidate 必填字段，"
                    "删除 schema 之外的字段后重新输出完整对象。"
                )
        if candidate is None:
            raise ContractViolation(
                code="EVOLUTION_CANDIDATE_OUTPUT_RETRY_EXHAUSTED",
                message=f"AI 连续 {_AI_CONTRACT_ATTEMPTS} 次未生成合规候选，本轮已留档。",
                context={
                    "model_name": selected.name,
                    "last_error_code": last_error.code if last_error else "",
                    "last_error": last_error.message if last_error else "",
                },
                blocking=False,
                suggested_action="系统可在后续研究轮次继续，不应切换模型或放宽 schema。",
            )
        proposal, _ = ProposalStore().create(
            role="factor_candidate",
            source=ProposalSource.API_MODEL,
            payload=payload,
            model_name=candidate.model_name,
        )
        code_tag = hashlib.sha256(candidate.code.encode("utf-8")).hexdigest()[:8]
        version_id = f"v{iteration:03d}-{code_tag}"
        return FactorVersion(
            version_id=version_id,
            parent_version_id=current.version_id,
            factor_name=config.base_factor,
            assumption=candidate.assumption,
            direction=_positive_direction(candidate.direction),
            timescale=candidate.timescale,
            dependencies=list(candidate.dependencies),
            parameters=dict(candidate.parameters),
            standardization=list(candidate.standardization),
            missing_handling=candidate.missing_handling,
            code=candidate.code,
            change_type=change_type,
            change_summary=change_summary,
            expected_impact=expected,
            model_name=candidate.model_name,
            generation_attempts=attempts,
            proposal_id=proposal.proposal_id,
        )

    @staticmethod
    def _require_single_change(
        current: FactorVersion, candidate: FactorCandidate, change_type: str
    ) -> None:
        direction = _positive_direction(candidate.direction)
        unchanged = {
            "direction": direction == current.direction,
            "standardization": list(candidate.standardization) == current.standardization,
            "missing": candidate.missing_handling == current.missing_handling,
            "code": candidate.code.strip() == current.code.strip(),
            "dependencies": list(candidate.dependencies) == current.dependencies,
        }
        required_unchanged: dict[str, tuple[str, ...]] = {
            "formula": ("direction", "standardization", "missing"),
            "lookback": ("direction", "standardization", "missing"),
            "market_regime": ("direction", "standardization", "missing"),
            "orthogonalization": ("direction", "standardization", "missing"),
            "winsorization": ("direction", "missing", "code", "dependencies"),
            "neutralization": ("direction", "missing", "code", "dependencies"),
            "missing": ("direction", "dependencies"),
            "direction": ("standardization", "missing", "code", "dependencies"),
        }
        changed_elsewhere = [
            name for name in required_unchanged[change_type] if not unchanged[name]
        ]
        if changed_elsewhere:
            raise ContractViolation(
                code="EVOLUTION_MULTIPLE_CHANGES",
                message=(
                    f"候选声明只改 {change_type}，但同时改变了：{', '.join(changed_elsewhere)}。"
                ),
                context={"change_type": change_type, "extra_changes": changed_elsewhere},
                blocking=False,
                suggested_action="下一轮从父版本复制其它字段，只保留一个改动维度。",
            )

    def check_candidate(
        self, config: EvolutionConfig, version: FactorVersion, *, timeout_seconds: float
    ) -> None:
        from ai.execution import syntax_check
        from panel import panel_field_names

        errors = syntax_check(version.code)
        if errors:
            raise QuantTrainingError(
                code="EVOLUTION_SYNTAX_INVALID",
                message="；".join(errors),
                blocking=False,
                suggested_action="下一轮修复静态检查错误。",
            )
        unknown = sorted(set(version.dependencies) - set(panel_field_names()))
        if unknown:
            raise ContractViolation(
                code="EVOLUTION_DEPENDENCY_UNKNOWN",
                message=f"候选引用未注册面板字段：{unknown}",
                blocking=False,
                suggested_action="下一轮只使用面板字段注册表中的依赖。",
            )
        from quantlens.research_candidate import publish_candidate
        key=version.version_id+':'+version.code_hash
        if key not in self._published_candidates:
            self._published_candidates[key]=publish_candidate(config.base_factor,version)

    def evaluate(
        self,
        config: EvolutionConfig,
        version: FactorVersion,
        *,
        window_name: str,
        window: ResearchWindow | None,
        timeout_seconds: float,
        on_progress,
    ) -> WindowResult:
        from quantlens.compute import submit
        from backtest.report import BacktestRunStore
        from strategy import StrategyProfileStore
        from strategy.models import StrategyProfileDoc,FactorSpec,FactorRef,WeightMode
        import uuid
        protocol,base_profile=self._context(config)
        if window is None:
            raise ContractViolation(code='EVOLUTION_WINDOW_REQUIRED',message='评价必须指定时间窗口')
        key=version.version_id+':'+version.code_hash
        if key not in self._published_candidates:
            self.check_candidate(config,version,timeout_seconds=timeout_seconds)
        name=self._published_candidates[key]
        from backtest.config import BacktestConfig
        from backtest.engine import run_backtest
        profile=base_profile.model_copy(update={'factors':FactorSpec(weighting=WeightMode.EQUAL,items=[FactorRef(name=name)])})
        run_id='BT-'+uuid.uuid4().hex
        on_progress('读取已发布的候选因子与统一行情，执行逐日回测')
        run_backtest(BacktestConfig(profile_name=profile.profile_name,start=window.start,end=window.end,initial_capital=config.initial_capital),protocol=protocol,profile=profile,run_id=run_id)
        record=BacktestRunStore.open_run(run_id)
        return WindowResult(window=window_name,run_id=run_id,metrics=record.read_metrics())

    def begin_experiment(
        self, config: EvolutionConfig, version: FactorVersion, parent_experiment_id: str
    ) -> str:
        from experiments import ExperimentStore

        record = ExperimentStore().register(
            subject_name=config.base_factor,
            subject_type="factor",
            change_summary=version.change_summary,
            change_reason=version.expected_impact or version.assumption,
            parent_exp_id=parent_experiment_id or None,
            train_window=config.train_window.text(),
            valid_window=config.validation_window.text(),
            test_window=config.test_window.text(),
            profile_name=config.profile_name,
        )
        return record.exp_id

    def record_result(
        self, experiment_id: str, result: VersionResult, *, improved: bool | None
    ) -> None:
        from experiments import ExperimentStore

        store = ExperimentStore()
        validation = result.validation
        assert validation is not None
        primary_train = result.folds[-1].train if result.folds else result.train
        primary_validation = result.folds[-1].validation if result.folds else result.validation
        store.append_event(
            experiment_id,
            "result",
            {
                "run_id": primary_validation.run_id,
                "train_run_id": primary_train.run_id if primary_train else "",
                "validation_run_id": primary_validation.run_id,
                "aggregate_train_run_id": result.train.run_id if result.train else "",
                "aggregate_validation_run_id": validation.run_id,
                "metrics": validation.metrics,
                "train_metrics": result.train.metrics if result.train else {},
                "oos_stability": result.stability,
                "constraints_passed": result.constraints_passed,
                "test_run_id": result.test.run_id if result.test else "",
                "test_metrics": result.test.metrics if result.test else {},
                "walk_forward_folds": [fold.model_dump(mode="json") for fold in result.folds],
                "generalization": (
                    result.generalization.model_dump(mode="json") if result.generalization else None
                ),
            },
        )
        if improved is not None:
            store.append_event(
                experiment_id,
                "comparison",
                {
                    "improved": improved,
                    "primary_metric": "annualized_excess_return",
                    "current": result.score,
                },
            )

    def abort_experiment(self, experiment_id: str, *, reason: str, code: str) -> None:
        from experiments import ExperimentStore

        ExperimentStore().append_event(experiment_id, "abort", {"reason": reason, "code": code})

    def review(
        self,
        config: EvolutionConfig,
        version: FactorVersion,
        result: VersionResult,
        best_result: VersionResult,
        *,
        timeout_seconds: float,
    ) -> ReviewResult:
        from ai.router import AIRouter

        if result.test is not None or best_result.test is not None:
            raise ContractViolation(
                code="EVOLUTION_LOCKED_TEST_MODEL_LEAK",
                message="锁定测试结果不得进入模型复盘上下文。",
                blocking=True,
                suggested_action="终止当前会话并更换新的锁定测试区间。",
            )

        prompt = json.dumps(
            {
                "change": {
                    "type": version.change_type,
                    "summary": version.change_summary,
                    "expected_impact": version.expected_impact,
                },
                "result": result.model_dump(mode="json"),
                "best_result": best_result.model_dump(mode="json"),
                "targets": config.targets.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
        router = AIRouter(request_timeout=max(1.0, min(180.0, timeout_seconds)))
        selected = router.pick("structured_config", model_name=config.model_name or None)
        base_prompt = prompt
        last_error: ContractViolation | None = None
        payload: dict[str, Any] = {}
        value: dict[str, Any] = {}
        decision = ""
        assessment = ""
        attempts = 0
        for attempts in range(1, _AI_CONTRACT_ATTEMPTS + 1):
            try:
                payload = router.run_task(
                    task="structured_config",
                    prompt=prompt,
                    system=_SYSTEM_REVIEW,
                    model_name=selected.name,
                )
                if payload.get("config_kind") != "factor_evolution_review":
                    raise ContractViolation(
                        code="EVOLUTION_REVIEW_KIND_INVALID",
                        message="结果分析返回了错误的 config_kind。",
                        blocking=False,
                    )
                value = payload.get("proposed_value") or {}
                decision = str(value.get("decision") or "")
                assessment = str(value.get("assessment") or "").strip()
                if decision not in {"continue", "stop"} or not assessment:
                    raise ContractViolation(
                        code="EVOLUTION_REVIEW_INVALID",
                        message="结果分析缺少合法 decision 或 assessment。",
                        blocking=False,
                    )
                break
            except ContractViolation as exc:
                last_error = exc
                payload = {}
                if attempts >= _AI_CONTRACT_ATTEMPTS:
                    break
                prompt = (
                    f"{base_prompt}\n\n"
                    f"上一次结果复盘未通过结构化契约：[{exc.code}] "
                    f"{exc.message[:2500]}\n"
                    "这是同一轮的格式纠错，不得改写任何回测数值。"
                    "根对象只允许 config_kind、proposed_value、open_questions；"
                    "config_kind 必须为 factor_evolution_review；"
                    "proposed_value 只允许 decision、assessment、failure_reason、next_change。"
                    "version_id、experiment_id、change_summary、phenomenon_verification 和"
                    " market_condition_applicability 不得出现在根对象。"
                )
        if not payload:
            raise ContractViolation(
                code="EVOLUTION_REVIEW_OUTPUT_RETRY_EXHAUSTED",
                message=f"AI 连续 {_AI_CONTRACT_ATTEMPTS} 次未生成合规的结果复盘。",
                context={
                    "model_name": selected.name,
                    "attempts": attempts,
                    "last_error_code": last_error.code if last_error else "",
                    "last_error": last_error.message if last_error else "",
                },
                blocking=False,
                suggested_action="保留已完成的量化评价并继续研究，同时将复盘格式失败记入审计事件。",
            )
        return ReviewResult(
            decision=decision,
            assessment=assessment,
            failure_reason=str(value.get("failure_reason") or ""),
            next_change=str(value.get("next_change") or ""),
            model_name=str(payload.get("model_name") or selected.name),
            generation_attempts=attempts,
        )

    def record_review(self, experiment_id: str, review: ReviewResult, *, improved: bool) -> None:
        from experiments import ExperimentStore

        store = ExperimentStore()
        if review.ai_generated:
            store.append_event(experiment_id, "ai_analysis", review.model_dump(mode="json"))
        else:
            store.append_event(
                experiment_id,
                "note",
                {
                    "note": review.assessment,
                    "model_name": review.model_name,
                    "generation_attempts": review.generation_attempts,
                    "source": "review_contract_fallback",
                },
            )
        store.append_event(
            experiment_id,
            "conclusion",
            {
                "improved": improved,
                "failure_reason": review.failure_reason,
                "next_steps": [review.next_change] if review.next_change else [],
                "decision": review.decision,
                "source": "auto_evolution" if review.ai_generated else "objective_result_fallback",
            },
        )

    def record_locked_test(
        self,
        experiment_id: str,
        result: VersionResult,
        *,
        passed: bool,
        stability: float,
    ) -> None:
        from experiments import ExperimentStore

        assert result.test is not None
        ExperimentStore().append_event(
            experiment_id,
            "conclusion",
            {
                "improved": passed,
                "decision": "frozen" if passed else "stopped",
                "failure_reason": "" if passed else "锁定测试未达到固定约束",
                "next_steps": [],
                "locked_test_run_id": result.test.run_id,
                "locked_test_stability": stability,
                "source": "auto_evolution",
            },
        )

__all__ = ["ProductionEvolutionServices"]
