from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Any

import pandas as pd

from ai.schemas import FactorCandidate
from exceptions import ContractViolation, QuantTrainingError
from quantlens.backtest_publication import serialized_publication

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
direction 只能是 positive 或 negative。父版本的方向字段已按此枚举提供。
必须做会改变因子计算或排序的实质修改；更名、注释、枚举别名或研究元数据变化不构成新候选。
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
_SYSTEM_PROPOSE += "\n只输出合法 JSON 对象，代码字符串中的换行必须转义为 \\n。FactorCandidate JSON Schema：\n" + json.dumps(FactorCandidate.model_json_schema(), ensure_ascii=False)

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
样本切分或风控约束，不得承诺未来收益。
当前复盘阶段仅包含固定训练区间的结果。validation/test 为空表示尚未评估，
不得推测验证指标或声称样本外有效。constraints_passed=false 表示尚未通过最终准入，
训练约束请依据 train.metrics.objective.eligible 与实际收益、回撤、换手判断。"""

def _positive_direction(direction: str) -> str:
    mapping = {
        "long_only_positive": "positive",
        "long_only_negative": "negative",
        "long_short": "undecided",
        "undecided": "undecided",
    }
    return mapping.get(direction, direction)

def _candidate_parent(version: FactorVersion) -> dict[str, Any]:
    
    return {
        "factor_name": version.factor_name,
        "display_name": "",
        "assumption": version.assumption,
        "direction": _positive_direction(version.direction),
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
    def summarize_experiment(self, config, evidence):
        from lifecycle.experiment_summary import generate_summary
        return generate_summary(evidence, model_name=config.model_name or None)

    def __init__(self, on_progress=None, *, analyst=None) -> None:
        self._profiles: dict[str, Any] = {}
        self._published_candidates: dict[str, str] = {}
        self._published_bounds: dict[str, tuple[str, str]] = {}
        self._supervision_panels: dict[tuple, dict[str, pd.DataFrame]] = {}
        self._candidate_audits: dict[str, dict[str, Any]] = {}
        self._preflight_configs: set[str] = set()
        self._on_progress = on_progress
        self._analyst = analyst
        self._signal_diagnostics: dict[str, dict] = {}

    def _context(self, config: EvolutionConfig):
        from backtest.config import BacktestConfig, resolve_and_check
        from backtest.protocol import verify_protocol_or_raise

        protocol = verify_protocol_or_raise()
        limits = protocol.objective.constraints
        if (config.targets.max_drawdown > limits.max_drawdown
                or config.targets.max_annualized_turnover > limits.max_turnover):
            raise ContractViolation(code='EVOLUTION_TARGETS_PROTOCOL_CONFLICT',
                message='研究门槛宽于锁定协议，必须统一回撤与换手约束后重新创建研究。', blocking=True)
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
            profile = resolve_and_check(bt, protocol, benchmark_key=config.content_hash(),
                                        benchmark_end=config.test_window.end, model_name=config.model_name or None)
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
        self._ensure_research_inputs(config)
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

    def _ensure_research_inputs(self, config: EvolutionConfig) -> None:
        key = config.content_hash()
        if key not in self._preflight_configs:
            from quantlens.compute import submit

            if self._on_progress:
                self._on_progress('检查训练/验证期的样本外风格簇覆盖，锁定测试保持封存')
            submit('spark_research_preflight', '--start', config.train_window.start,
                   '--end', config.validation_window.end, reporter=self._on_progress)
            self._preflight_configs.add(key)

    def candidate_panel_fields(self) -> list[str]:
        from panel import panel_field_names
        return panel_field_names()

    def _diagnose(self, config, current, memory, timeout_seconds):
        import time
        from backtest.report import BacktestRunStore
        from .analysis import analyze
        from .diagnostics import comparison_contract, digest, fail
        from .selection import definition_key
        from ai.router import AIRouter

        if any(item.get('metrics', {}).get('validation') or item.get('metrics', {}).get('test') for item in memory):
            fail('验证/测试记忆不得进入训练分析。')
        previous = next((item for item in reversed(memory) if item.get('version_id') == current.version_id
                         and item.get('metrics', {}).get('train', {}).get('run_id')), None)
        if previous is None:
            fail('没有父版本的训练回测，禁止无证据生成候选。')
        run_id = previous['metrics']['train']['run_id']
        if not re.fullmatch(r'BT-[A-Za-z0-9._-]+', run_id):
            fail('训练回测标识非法。')
        record = BacktestRunStore.open_run(run_id)
        path = record.run_dir / 'training_diagnostics.json'
        if not path.exists():
            fail('父版本缺少新框架的训练证据，须按新协议重新计算，不能用旧汇总替代。')
        evidence = json.loads(path.read_text())
        protocol, profile = self._context(config)
        expected = digest(comparison_contract(config, protocol.model_dump(mode='json'), profile.model_dump(mode='json')))
        if (evidence['run_id'] != run_id or evidence['definition_hash'] != definition_key(current)
                or evidence['config_hash'] != config.content_hash() or evidence['comparison_hash'] != expected
                or record.read_status().get('state') != 'completed'):
            fail('训练证据与父版本、资金、基准或研究配置不一致。')
        for name, sha in evidence['artifact_hashes'].items():
            if name not in {'config.yaml', 'daily/nav.parquet', 'daily/trades.parquet', 'daily/positions.parquet'}:
                fail('训练证据包含未授权的产物路径。')
            if hashlib.sha256((record.run_dir / name).read_bytes()).hexdigest() != sha:
                fail('训练回测产物在诊断后改变。')
        def respond(context, remaining):
            if self._analyst is not None:
                return self._analyst(context, remaining)
            router = AIRouter(request_timeout=max(1., min(180., remaining)))
            payload = router.run_task(task='structured_config', model_name=config.model_name or None,
                system=('你是因子研究分析者。只依据训练证据提出可证伪的单一改动，或者继续查询/停止。'
                        '金额归因不证明经济因果，整体市场参照不用于排名，现金仓位差异不能冒充选股能力。'
                        '资金不足、最低佣金或组合问题不能靠修改因子伪装解决；此时可停止并说明需要组合研究。'
                        '必须完整照抄引用指标的 evidence_id、path 和 value；禁止推测验证/测试结果。'
                        '输出 StructuredConfig，config_kind=factor_training_analysis，proposed_value 严格满足给出的 schema。'),
                prompt=json.dumps(context, ensure_ascii=False, allow_nan=False))
            if payload.get('config_kind') != 'factor_training_analysis':
                fail('分析输出的任务类型错误。')
            return payload['proposed_value']
        started = time.monotonic()
        result = analyze(evidence, respond, max_turns=config.analysis_max_turns,
                         timeout_seconds=timeout_seconds, on_progress=self._on_progress)
        result['elapsed_seconds'] = time.monotonic() - started
        result['analyst'] = 'development' if self._analyst is not None else 'configured_ai'
        log = record.run_dir / 'analysis_dialogues.jsonl'
        with log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
        return result

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
        from panel import panel_field_names
        from factors.registry import FactorRegistry

        diagnosis = self._diagnose(config, current, memory, timeout_seconds)
        if diagnosis['decision']['action'] == 'stop':
            raise QuantTrainingError(code='EVOLUTION_ANALYST_STOP',
                message=diagnosis['decision']['stop_reason'], blocking=True)
        timeout_seconds -= diagnosis['elapsed_seconds']
        if timeout_seconds <= 0:
            raise QuantTrainingError(code='EVOLUTION_ANALYSIS_TIMEOUT', message='分析已耗尽本轮预算。', blocking=True)
        prompt = json.dumps(
            {
                "research": {
                    "stage": "training_only_search",
                    "base_factor": config.base_factor,
                    "iteration": iteration,
                    "random_seed": config.random_seed,
                    "fixed_windows": {
                        "train": config.train_window.text(),
                        "validation": "hidden_until_finalist_frozen",
                        "locked_test": "hidden_until_final_version",
                    },
                    "walk_forward": config.walk_forward.model_dump(mode="json"),
                    "targets": config.targets.model_dump(mode="json"),
                },
                "parent_version": _candidate_parent(current),
                "registered_diagnosis": diagnosis,
                "panel_contract": {"available_fields": self.candidate_panel_fields(),
                                   "min_history": FactorRegistry().get(config.base_factor).min_history,
                                   "execution": "compute(panel)，只使用声明字段；必须通过未来截断及分块预热检查"},
                "session_memory": memory[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        router = AIRouter(request_timeout=max(1.0, min(180.0, timeout_seconds)))
        selected = router.pick("factor_candidate", model_name=config.model_name or None)
        base_prompt = prompt
        last_error: QuantTrainingError | None = None
        candidate: FactorCandidate | None = None
        payload: dict[str, Any] = {}
        change_type = ""
        change_summary = ""
        expected = ""
        attempts = 0
        for attempts in range(1, _AI_CONTRACT_ATTEMPTS + 1):
            try:
                if self._on_progress:
                    self._on_progress(f"第 {iteration} 轮候选请求：{selected.name}（尝试 {attempts}/{_AI_CONTRACT_ATTEMPTS}）")
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
                if change_type != diagnosis['decision']['change_type']:
                    raise ContractViolation(code='EVOLUTION_HYPOTHESIS_CHANGED',
                        message='候选改动类型与事先登记的诊断假设不同。', blocking=False)
                self._require_single_change(current, candidate, change_type)
                break
            except QuantTrainingError as exc:
                if not isinstance(exc, ContractViolation) and exc.code not in {"AI_OUTPUT_NOT_JSON", "AI_OUTPUT_NOT_OBJECT"}:
                    raise
                last_error = exc
                candidate = None
                if self._on_progress:
                    self._on_progress(f"候选检查未通过 [{exc.code}]：{exc.message}；保留本轮并在尝试上限内纠正。")
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
        version_identity = {
            "code": candidate.code, "direction": _positive_direction(candidate.direction),
            "dependencies": candidate.dependencies, "standardization": candidate.standardization,
            "missing_handling": candidate.missing_handling,
            "parameters": {key: value for key, value in candidate.parameters.items() if not key.startswith("_")},
        }
        version_tag = hashlib.sha256(json.dumps(version_identity, sort_keys=True).encode("utf-8")).hexdigest()[:8]
        version_id = f"v{iteration:03d}-{version_tag}"
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
            research_plan=diagnosis,
        )

    @staticmethod
    def _require_single_change(
        current: FactorVersion, candidate: FactorCandidate, change_type: str
    ) -> None:
        direction = _positive_direction(candidate.direction)
        unchanged = {
            "direction": direction == _positive_direction(current.direction),
            "standardization": list(candidate.standardization) == current.standardization,
            "missing": candidate.missing_handling == current.missing_handling,
            "code": candidate.code.strip() == current.code.strip(),
            "dependencies": list(candidate.dependencies) == current.dependencies,
        }
        import ast
        try:
            same_code = ast.dump(ast.parse(candidate.code), include_attributes=False) == ast.dump(ast.parse(current.code), include_attributes=False)
        except SyntaxError:
            same_code = unchanged["code"]
        if same_code and all(unchanged[name] for name in ("direction", "standardization", "missing", "dependencies")):
            raise ContractViolation(
                code="EVOLUTION_NO_EFFECTIVE_CHANGE", message="候选没有改变公式、方向、依赖、标准化或缺失处理；枚举别名和研究元数据不算实质改动。",
                blocking=False, suggested_action="保持同一金融假设，选择一个会改变实际因子计算或排序的维度。",
            )
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
        from .supervision import check_source

        check_source(version.code)
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
            # Resumed sessions may skip load_base; they must pass the same gate.
            self._ensure_research_inputs(config)
            self._audit_candidate(config, version)
            if self._on_progress:
                self._on_progress(f"计算并发布候选因子 {version.version_id}")
            self._published_candidates[key]=publish_candidate(
                config.base_factor,version,start=config.train_window.start,
                end=config.train_window.end,reporter=self._on_progress)
            self._published_bounds[key] = (config.train_window.start, config.train_window.end)

    def _publish_for_window(self, config, version, start, end, timeout_seconds):
        self.check_candidate(config, version, timeout_seconds=timeout_seconds)
        key = version.version_id + ':' + version.code_hash
        previous = self._published_bounds[key]
        if previous[0] <= start and previous[1] >= end:
            return
        from quantlens.research_candidate import publish_candidate
        lower, upper = min(start, previous[0]), max(end, previous[1])
        self._published_candidates[key] = publish_candidate(
            config.base_factor, version, start=lower, end=upper, reporter=self._on_progress)
        self._published_bounds[key] = (lower, upper)

    def _audit_candidate(self, config: EvolutionConfig, version: FactorVersion) -> None:
        from factors.registry import FactorRegistry
        from quantlens.research_data import extract
        from .supervision import audit_factor

        history = FactorRegistry().get(config.base_factor).min_history
        end = pd.Timestamp(config.train_window.end)
        start = max(pd.Timestamp(config.train_window.start),
                    end - pd.Timedelta(days=max(550, (history + 20) * 3)))
        fields = tuple(sorted(set(version.dependencies)))
        cache_key = (config.content_hash(), fields, str(start.date()), str(end.date()))
        if cache_key not in self._supervision_panels:
            if self._on_progress:
                self._on_progress("读取训练期完整横截面，检查未来截断与分块预热一致性")
            frame = extract({'kind': 'panel', 'fields': list(fields),
                             'start': str(start.date()), 'end': str(end.date())})
            dates = pd.to_datetime(frame['date'])
            if (frame.empty or not dates.between(start, end).all()
                    or frame.duplicated(['date', 'code']).any()):
                raise ContractViolation(code='EVOLUTION_AUDIT_INPUT_INVALID',
                    message='监督面板为空、日期越出训练区间，或日期/证券键重复。', blocking=True)
            frame = frame.assign(date=dates)
            self._supervision_panels[cache_key] = {
                name: frame.pivot(index='date', columns='code', values=name).sort_index()
                for name in fields}
        # Keep the complete cross section, but bound dates to the warmup and
        # three causal cut points. Large redundant histories exhaust the worker
        # address-space limit before a candidate can reach a backtest.
        audit_rows = max(history + 4, 4 * history // 3 + 8)
        sample = {name: value.iloc[-audit_rows:]
                  for name, value in self._supervision_panels[cache_key].items()}
        report = audit_factor(version.code, sample, min_history=history)
        report.update(start=str(start.date()), end=str(end.date()),
                      training_only=True, code_hash=version.code_hash)
        self._candidate_audits[version.version_id + ':' + version.code_hash] = report
        if self._on_progress:
            self._on_progress('因子监督检查通过：' + json.dumps(report, ensure_ascii=False))

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
        from backtest.report import BacktestRunStore
        from strategy.models import FactorSpec,FactorRef,WeightMode
        import uuid
        protocol,base_profile=self._context(config)
        if window is None:
            raise ContractViolation(code='EVOLUTION_WINDOW_REQUIRED',message='评价必须指定时间窗口')
        key=version.version_id+':'+version.code_hash
        self._publish_for_window(config, version, window.start, window.end, timeout_seconds)
        name=self._published_candidates[key]
        from backtest.config import BacktestConfig
        from backtest.engine import run_backtest
        profile=base_profile.model_copy(update={'factors':FactorSpec(weighting=WeightMode.EQUAL,items=[FactorRef(name=name)])})
        signal = None
        if window_name == 'fixed_train':
            from quantlens.research_data import extract
            from .diagnostics import digest, signal_summary
            identity = digest({'config': config.content_hash(), 'profile': profile.model_dump(mode='json'),
                               'code_hash': version.code_hash})
            if identity not in self._signal_diagnostics:
                on_progress('先评价训练期预测能力：次日收盘入场，标签完整成熟后才纳入')
                daily = extract({'kind': 'signal_diagnostics', 'profile': profile.model_dump(mode='json'),
                                 'start': config.train_window.start, 'end': config.train_window.end,
                                 'horizons': config.prediction_horizons})
                self._signal_diagnostics[identity] = signal_summary(daily,
                    start=config.train_window.start, end=config.train_window.end, horizons=config.prediction_horizons)
            signal = self._signal_diagnostics[identity]
        run_id='BT-'+uuid.uuid4().hex
        on_progress('读取已发布的候选因子与统一行情，执行逐日回测')
        run_backtest(BacktestConfig(profile_name=profile.profile_name,start=window.start,end=window.end,initial_capital=config.initial_capital,lookback_buffer_days=config.lookback_buffer_days),protocol=protocol,profile=profile,run_id=run_id,console=_LogConsole(on_progress))
        record=BacktestRunStore.open_run(run_id)
        metrics = record.read_metrics()
        if window_name == 'fixed_train':
            self._write_training_evidence(config, version, record, protocol, base_profile, signal, metrics)
        return WindowResult(window=window_name,run_id=run_id,metrics=metrics)

    def _write_training_evidence(self, config, version, record, protocol, profile, signal, metrics):
        import yaml
        from .diagnostics import comparison_contract, digest, summarize_training
        from .selection import definition_key
        contract = comparison_contract(config, protocol.model_dump(mode='json'), profile.model_dump(mode='json'))
        frames = [pd.read_parquet(record.run_dir / 'daily' / f'{name}.parquet')
                  for name in ('nav', 'trades', 'positions')]
        sections = summarize_training(*frames, start=config.train_window.start, end=config.train_window.end,
            capital=config.initial_capital, benchmark=contract['benchmark_plan'])
        sections['signal'] = signal
        evidence = {'run_id': record.run_id, 'config_hash': config.content_hash(),
                    'definition_hash': definition_key(version), 'comparison_hash': digest(contract),
                    'contract': contract, 'sections': sections,
                    'artifact_hashes': {name: hashlib.sha256((record.run_dir / name).read_bytes()).hexdigest()
                                       for name in ('config.yaml', 'daily/nav.parquet', 'daily/trades.parquet', 'daily/positions.parquet')}}
        text = json.dumps(evidence, ensure_ascii=False, allow_nan=False)
        pending = record.run_dir / 'training_diagnostics.pending'
        pending.write_text(text, encoding='utf-8')
        pending.replace(record.run_dir / 'training_diagnostics.json')
        metrics['research_comparison_hash'] = evidence['comparison_hash']
        raw = yaml.safe_load((record.run_dir / 'config.yaml').read_text())
        snapshot = (raw.get('data_fingerprint') or {}).get('data_snapshot')
        if not snapshot:
            from .diagnostics import fail
            fail('训练回测未记录输入快照，禁止候选比较。')
        metrics['research_input_hash'] = digest(sorted(snapshot) if isinstance(snapshot, list) else [snapshot])
        kind = contract['benchmark_plan']['return_benchmark']['kind']
        if kind not in {'same_cluster_equal_weight', 'industry_equal_weight'}:
            metrics['research_benchmark_return_hash'] = digest(frames[0][['date', 'benchmark_return']].to_dict('records'))
        metrics['prediction_diagnostics'] = signal
        record.write_metrics(metrics)

    @serialized_publication
    def evaluate_windows(
        self, config, version, *, windows, research_id, timeout_seconds,
        on_progress, on_window_started, on_window_completed,
    ):
        import json
        import time
        import uuid
        from backtest.config import BacktestConfig
        from backtest.report import BacktestRunStore
        from quantlens.compute import submit
        from quantlens.sync_ads import sync
        from strategy.models import FactorSpec, FactorRef, WeightMode

        deadline = time.time() + timeout_seconds
        protocol, base_profile = self._context(config)
        key = version.version_id + ':' + version.code_hash
        self._publish_for_window(config, version, min(window.start for _, window in windows),
                                 max(window.end for _, window in windows), timeout_seconds)
        profile = base_profile.model_copy(update={'factors': FactorSpec(
            weighting=WeightMode.EQUAL, items=[FactorRef(name=self._published_candidates[key])])})
        requests = [{
            'window': name, 'run_id': 'BT-' + uuid.uuid4().hex,
            'config': BacktestConfig(profile_name=profile.profile_name, start=window.start, end=window.end,
                                     initial_capital=config.initial_capital,
                                     lookback_buffer_days=config.lookback_buffer_days).model_dump(mode='json'),
        } for name, window in windows]
        expected = {item['window']: item['run_id'] for item in requests}
        batch_id = 'research-backtest-' + uuid.uuid4().hex
        expected_batches = {item['window']: f'{batch_id}-{index:02d}' for index, item in enumerate(requests)}
        results = {}
        callback_errors = []

        def report(line):
            # Drain the subprocess even if evidence persistence fails; surface the
            # original callback error after submission rather than losing its pipe.
            if callback_errors:
                return
            try:
                if line.startswith('QUANTLENS_WINDOW_STARTED='):
                    name = json.loads(line.partition('=')[2])['window']
                    if name not in expected:
                        raise ValueError('Unexpected research window')
                    on_window_started(name)
                elif line.startswith('QUANTLENS_WINDOW_COMPLETED='):
                    item = json.loads(line.partition('=')[2])
                    name, run_id = item['window'], item['run_id']
                    if (expected.get(name) != run_id or name in results
                            or item.get('batch') != expected_batches.get(name)):
                        raise ValueError('Unexpected or duplicate research result')
                    record = BacktestRunStore.open_run(run_id)
                    if record.read_status().get('state') != 'completed':
                        raise ValueError('Incomplete research backtest')
                    on_progress('同步已完成窗口的回测结果')
                    sync(item['batch'])
                    result = WindowResult(window=name, run_id=run_id, metrics=record.read_metrics())
                    results[name] = result
                    on_window_completed(name, result)
                else:
                    on_progress(line)
            except Exception as exc:
                callback_errors.append(exc)

        if hasattr(on_progress, 'progress'):
            report.progress = on_progress.progress

        on_progress(f'合并提交 {len(requests)} 个训练/验证窗口，共用 Spark 会话；每个窗口独立撮合。')
        submit('spark_backtest', '--windows', json.dumps(requests),
               '--profile', json.dumps(profile.model_dump(mode='json')),
               '--protocol', json.dumps(protocol.model_dump(mode='json')),
               '--deadline', str(deadline), '--research-id', research_id,
               '--batch', batch_id, reporter=report)
        if callback_errors:
            raise callback_errors[0]
        return results

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
        if validation is None:
            assert result.train is not None and result.test is None
            store.append_event(experiment_id, "note", {
                "stage": "training_only", "run_id": result.train.run_id,
                "metrics": result.train.metrics, "training_improved": improved,
                "text": "固定训练区间评估完成；该结果仅用于搜索，尚无验证或测试结论。",
            })
            return
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
            from .selection import quality_key
            store.append_event(
                experiment_id,
                "comparison",
                {
                    "improved": improved,
                    "primary_metric": "feasibility_profitability_calmar_stability",
                    "current": quality_key(result),
                },
            )

    def abort_experiment(self, experiment_id: str, *, reason: str, code: str) -> None:
        from experiments import ExperimentStore

        ExperimentStore().append_event(experiment_id, "abort", {"reason": reason, "code": code})

    def record_window_result(self, experiment_id, result, *, fold_id, window_name):
        from experiments import ExperimentStore

        label = "训练" if window_name == "train" else "验证"
        ExperimentStore().append_event(experiment_id, "note", {
            "text": f"{fold_id} {label}回测已完成，整组评估仍在进行。",
            "run_id": result.run_id, "metrics": result.metrics,
            "fold_id": fold_id, "window_name": window_name,
        })

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
        if result.validation is not None or best_result.validation is not None:
            raise ContractViolation(
                code="EVOLUTION_VALIDATION_MODEL_LEAK",
                message="冻结后的验证结果不得反馈给模型继续搜索。", blocking=True,
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
        last_error: QuantTrainingError | None = None
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
            except QuantTrainingError as exc:
                if not isinstance(exc, ContractViolation) and exc.code not in {"AI_OUTPUT_NOT_JSON", "AI_OUTPUT_NOT_OBJECT"}:
                    raise
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
                suggested_action="终止当前搜索并修正模型输出契约；保留已完成的有效量化结果。",
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
        if not review.ai_generated:
            raise ContractViolation(code="EVOLUTION_REVIEW_REQUIRED",
                                    message="缺少有效模型复盘，停止研究。", blocking=True)
        store.append_event(experiment_id, "ai_analysis", review.model_dump(mode="json"))
        store.append_event(
            experiment_id,
            "conclusion",
            {
                "improved": improved,
                "failure_reason": review.failure_reason,
                "next_steps": [review.next_change] if review.next_change else [],
                "decision": review.decision,
                "source": "auto_evolution",
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
