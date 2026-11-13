from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ai.router import AIRouter
from ai.schemas import FactorCandidate
from exceptions import ContractViolation, ModelRouteError, QuantTrainingError
from factors import FactorMetadata, FactorPlugin, FactorRegistry, FactorStatus, run_candidate
from factors.registry import hash_code
from panel import cache_status, load_panel, panel_field_names
from proposals import ProposalSource, ProposalStore
from strategy.models import FactorRef, FactorSpec, StrategyProfileDoc, StrategyStatus, WeightMode
from strategy.store import StrategyProfileStore

from ..ai_chat import chat_stream, pick_chat_model
from ..serialize import json_safe
from ..tasks import manager
from .factor_evolution import start_factor_evolution

router = APIRouter(tags=["factor-chat"])

_TASK = "factor_candidate"
_MAX_CANDIDATE_ATTEMPTS = 3
_ASCII_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,63}")

_DISCUSSION_SYSTEM = """\
你是因子研究顾问，正在帮一位投资者把金融现象整理成可证伪的研究假设。
始终用自然、简洁的中文，每轮最多问一个问题。需要弄清：
1. 用户观察到了什么金融现象；
2. 它为什么可能通过投资者行为、风险补偿、制度约束或信息扩散影响未来收益；
3. 大约多久兑现，什么情况下可能失效。
用户没有技术偏好时，直接给出一个保守的建议假设并说明理由。
不得询问代码、公式、YAML、JSON、数据字段、面板、缩尾、标准化、中性化或回测参数，
也不得向用户展示这些内部内容。信息足够时，用通俗语言总结现象、逻辑链、
预期持有期和失效条件，然后提示用户点击“开始自动研究”。
不得承诺收益，不得声称一个未评价的因子可以稳定盈利。
"""

_CANDIDATE_SYSTEM = """\
你是 QuantLens 的因子研究员。根据对话中的金融现象生成 FactorCandidate。
候选必须有清晰的经济机制和可证伪的失效条件，不得为追求历史表现堆叠算子。
factor_name 使用稳定的英文小写内部标识，display_name 使用简洁自然的中文。
代码提供 compute(panel) 入口，返回与面板轴一致的 pandas.DataFrame。
禁止使用未来数据；dependencies 列出研究所需的面板字段。
对基本面字段必须按 PIT 语义使用，不得以报告期充当当时可见日期。
不要输出 schema 之外的字段。
"""

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str

class FactorChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = ""
    messages: list[ChatMessage] = Field(default_factory=list, max_length=80)

def _conversation(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [
        {"role": item.role, "content": item.content.strip()}
        for item in messages
        if item.role in {"user", "assistant"} and item.content.strip()
    ]

def _conversation_text(messages: list[ChatMessage]) -> str:
    labels = {"user": "用户", "assistant": "AI"}
    return "\n".join(
        f"{labels[item['role']]}：{item['content']}" for item in _conversation(messages)
    )

def _factor_context() -> str:
    items = FactorRegistry().list()
    if not items:
        return "当前尚无已登记因子。"
    lines = ["当前因子（避免重复研究）："]
    for item in items:
        lines.append(f"- {item.display_name or item.name}：{item.assumption or '暂无假设说明'}")
    return "\n".join(lines)

@router.post("/factors/chat")
def factor_chat(payload: FactorChatPayload):
    ai_router = AIRouter()
    model = pick_chat_model(ai_router, payload.model.strip())
    messages = [{"role": "system", "content": _DISCUSSION_SYSTEM + "\n\n" + _factor_context()}]
    messages.extend(_conversation(payload.messages))
    return chat_stream(ai_router, model, messages)

def _sample_panel(dependencies: list[str]) -> dict[str, pd.DataFrame]:
    status = cache_status().get("default")
    if status is None or status.state != "fresh" or not status.meta:
        state = status.state if status is not None else "missing"
        raise ContractViolation(
            code="FACTOR_AI_PANEL_NOT_READY",
            message=f"当前计算缓存状态为 {state}，无法完成真实数据试运行。",
            blocking=True,
            suggested_action="先在数据页完成数据更新，系统会自动重建研究数据。",
        )
    bounds = status.meta.get("build_params") or {}
    raw_start, raw_end = str(bounds.get("start") or ""), str(bounds.get("end") or "")
    if not raw_start or not raw_end:
        raise ContractViolation(
            code="FACTOR_AI_PANEL_RANGE_MISSING",
            message="计算缓存缺少可试运行的时间范围。",
            blocking=True,
            suggested_action="在数据页重建计算缓存后重试。",
        )
    end = date.fromisoformat(raw_end)
    start = max(date.fromisoformat(raw_start), end - timedelta(days=550))
    
    return load_panel(
        dependencies,
        start=start.isoformat(),
        end=end.isoformat(),
        exclude_imputed=True,
    )

def _validate_candidate(candidate: FactorCandidate) -> None:
    if not _ASCII_NAME.fullmatch(candidate.factor_name):
        raise ContractViolation(
            code="FACTOR_AI_NAME_INVALID",
            message="内部因子标识必须是 3~64 位英文字母、数字或下划线。",
        )
    if not candidate.display_name.strip() or not re.search(r"[\u4e00-\u9fff]", candidate.display_name):
        raise ContractViolation(
            code="FACTOR_AI_DISPLAY_NAME_INVALID",
            message="展示名称必须是简洁、自然的中文。",
        )
    if not candidate.dependencies:
        raise ContractViolation(
            code="FACTOR_AI_DEPENDENCIES_EMPTY",
            message="候选未声明任何可核验的数据依赖。",
        )
    unknown = sorted(set(candidate.dependencies) - set(panel_field_names()))
    if unknown:
        raise ContractViolation(
            code="FACTOR_AI_DEPENDENCY_UNKNOWN",
            message=f"候选使用了不存在的数据字段：{'、'.join(unknown)}。",
        )
    existing = {item.name for item in FactorRegistry().list()}
    if candidate.factor_name in existing or FactorPlugin.default().path_for(candidate.factor_name).exists():
        raise ContractViolation(
            code="FACTOR_AI_NAME_EXISTS",
            message=f"内部标识 {candidate.factor_name} 已存在，必须换用唯一名称。",
        )
    profile_name = f"research_{candidate.factor_name}"
    if profile_name in {item.profile_name for item in StrategyProfileStore().list()}:
        raise ContractViolation(
            code="FACTOR_AI_PROFILE_NAME_EXISTS",
            message=f"研究配置 {profile_name} 已存在，必须换用唯一的因子标识。",
        )

def _check_candidate(candidate: FactorCandidate) -> None:
    panel = _sample_panel(list(candidate.dependencies))
    report = run_candidate(candidate, panel_payload=panel)
    value = report.value
    if not isinstance(value, pd.DataFrame) or value.empty:
        raise ContractViolation(
            code="FACTOR_AI_OUTPUT_INVALID",
            message="候选必须返回非空的日期×股票因子矩阵。",
        )

def _register_draft(candidate: FactorCandidate) -> tuple[str, str]:
    proposal, _ = ProposalStore().create(
        role=_TASK,
        source=ProposalSource.API_MODEL,
        payload=candidate.model_dump(mode="json"),
        model_name=candidate.model_name,
    )
    plugin = FactorPlugin.default()
    plugin_path = plugin.write(candidate.factor_name, candidate.code, override=False)

    from session import has_active_market, load_session

    session = load_session()
    markets = [session.active_market] if has_active_market(session) else []
    instruments = [session.active_instrument_class] if has_active_market(session) else []
    direction = {
        "long_only_positive": "positive",
        "long_only_negative": "negative",
        "long_short": "undecided",
        "undecided": "undecided",
    }[candidate.direction]
    metadata = FactorMetadata(
        name=candidate.factor_name,
        display_name=candidate.display_name,
        assumption=candidate.assumption,
        applicable_markets=markets,
        applicable_instruments=instruments,
        timescale=candidate.timescale,
        dependencies=list(candidate.dependencies),
        output_frequency=candidate.timescale,
        direction=direction,
        parameters=dict(candidate.parameters),
        standardization=list(candidate.standardization),
        missing_handling=candidate.missing_handling,
        ai_generated=True,
        production_permission=False,
        risk_notes=(
            "该因子由 AI 根据金融现象生成，尚未获得实盘权限。"
            + (f"模型自评：{candidate.rationale}" if candidate.rationale else "")
        ),
        evaluation_metrics=list(candidate.evaluation_metric_hints),
        status=FactorStatus.DRAFT,
        code_hash=hash_code(candidate.code),
        plugin_path=str(plugin_path.relative_to(plugin.ws.root)),
    )
    FactorRegistry().upsert(metadata, override=False)

    profile_name = f"research_{candidate.factor_name}"
    StrategyProfileStore().upsert(
        StrategyProfileDoc(
            profile_name=profile_name,
            display_name=f"{candidate.display_name}自动研究",
            inheritance_parent="a_share_research_base",
            status=StrategyStatus.DRAFT,
            factors=FactorSpec(
                weighting=WeightMode.EQUAL,
                items=[FactorRef(name=candidate.factor_name)],
            ),
            extra={"purpose": "factor_research_only", "internal_only": True},
        ),
        override=False,
    )
    return proposal.proposal_id, profile_name

def _candidate_prompt(payload: FactorChatPayload, previous_error: str = "") -> str:
    correction = ""
    if previous_error:
        correction = (
            "\n\n上一个候选未通过系统检查：\n"
            f"{previous_error}\n请保留金融假设，修正候选后重新输出完整对象。"
        )
    return (
        "根据以下讨论生成一个可安全评价的初始因子候选：\n"
        f"{_conversation_text(payload.messages)}\n\n"
        f"{_factor_context()}\n\n"
        "可用数据字段（仅供内部生成代码）：\n"
        + "、".join(panel_field_names())
        + correction
    )

@router.post("/factors/chat/research")
def start_factor_chat_research(payload: FactorChatPayload) -> dict:
    if not any(item.role == "user" and item.content.strip() for item in payload.messages):
        raise ContractViolation(
            code="FACTOR_AI_DISCUSSION_EMPTY",
            message="请先填写研究想法。",
            suggested_action="例如说明哪类公司、什么变化可能在未来几周或几个月被市场重新定价。",
        )
    ai_router = AIRouter()

    selected = ai_router.pick(_TASK, model_name=payload.model.strip() or None)

    ai_router.pick("structured_config", model_name=selected.name)

    def run(report) -> dict:
        report.progress({"state": "running", "step": "理解金融现象", "percent": 8.0})
        report("已将讨论整理为可证伪的金融假设。")
        last_error = ""
        candidate: FactorCandidate | None = None
        for attempt in range(1, _MAX_CANDIDATE_ATTEMPTS + 1):
            report.progress(
                {
                    "state": "running",
                    "step": f"生成并完善候选（{attempt}/{_MAX_CANDIDATE_ATTEMPTS}）",
                    "percent": 15.0 + attempt * 12.0,
                }
            )
            try:
                raw = ai_router.run_task(
                    task=_TASK,
                    prompt=_candidate_prompt(payload, last_error),
                    system=_CANDIDATE_SYSTEM,
                    model_name=selected.name,
                )
                candidate = FactorCandidate.model_validate(raw)
                _validate_candidate(candidate)
                report.progress(
                    {"state": "running", "step": "语法检查与真实数据试运行", "percent": 62.0}
                )
                _check_candidate(candidate)
                report(f"候选“{candidate.display_name}”已通过语法检查和真实数据试运行。")
                break
            except ModelRouteError:
                raise
            except QuantTrainingError as exc:
                if exc.code.startswith("PANEL_") or exc.code.startswith("FACTOR_AI_PANEL_"):
                    
                    raise
                last_error = f"[{exc.code}] {exc.message}"
                report(f"第 {attempt} 版候选未通过，AI 将自动修正：{exc.message}")
                candidate = None
        if candidate is None:
            raise ContractViolation(
                code="FACTOR_AI_CANDIDATE_EXHAUSTED",
                message=f"连续 {_MAX_CANDIDATE_ATTEMPTS} 次仍未生成通过检查的候选。",
                blocking=True,
                suggested_action="补充金融现象的逻辑链或失效条件后重新发起研究。",
            )

        report.progress({"state": "running", "step": "登记研究候选", "percent": 78.0})
        proposal_id, profile_name = _register_draft(candidate)
        report("候选已作为不可直接交易的研究版本登记。")

        report.progress({"state": "running", "step": "启动自动评价与迭代", "percent": 90.0})
        evolution = start_factor_evolution(candidate.factor_name, model_name=selected.name)
        report("已启动训练、滚动验证、父版本对比与失效风险检查。")
        return json_safe(
            {
                "factor_name": candidate.factor_name,
                "display_name": candidate.display_name,
                "assumption": candidate.assumption,
                "proposal_id": proposal_id,
                "profile_name": profile_name,
                "research_id": evolution["research_id"],
                "evolution_task_id": evolution["task_id"],
                "program_id": evolution["program_id"],
                "status": "已进入自动评价与迭代",
            }
        )

    task_id = manager.start("新因子自动研究", run, resource="factor-ai-research")
    return {"task_id": task_id, "model": selected.name}

__all__ = ["router"]
