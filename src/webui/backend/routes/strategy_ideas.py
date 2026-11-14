"""Discuss strategy ideas with AI; execute plans with local quantitative models."""
from __future__ import annotations

import json
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai import secrets
from ai.matrix import load_matrix
from ai.permissions import score_model_for_task
from ai.registry import ModelKind, ModelRegistry
from ai.router import AIRouter, env_var_name
from exceptions import ContractViolation, ModelRouteError
from factors.registry import FactorRegistry, FactorStatus
from strategy import StrategyProfileStore
from strategy.research import ResearchSpec, create_strategy, defaults, is_research, require_strategy, start_research
from strategy.training import METHODS
from ..ai_chat import chat_stream
from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=['strategy-research'])

_DISCUSSION_SYSTEM = '''你是策略研究顾问，帮助用户把投资想法整理成可检验的多因子策略。
用自然、简洁的中文，每轮最多问一个问题。讨论想选哪类公司、因子之间为什么互补、
预期持有时间和失效条件。依据可用因子的经济含义给出建议，不要求用户填写技术参数。
不询问代码、JSON、YAML、数据字段或训练配置，不在对话中输出内部配置。
用户没有明确偏好时，建议保守的研究方案，计算区间使用系统默认值。
首先引导用户在账户选择框中选择关联账户，未选时请先提醒选择，不替用户决定账户。
策略创建后账户不可更换，一个账户可研究多个策略；各策略是独立方案，不叠加资金。
资金和科创板权限来自账户，不询问手填本金。解释小账户整手、最低佣金、持仓与调仓频率的关系。
只能组合系统提供的启用或草稿因子；草稿仅供研究，策略启用仍要求所有因子已启用。
语言模型只整理方案，不分配数值权重，不编造选股或回测结果。
信息足够后总结策略逻辑、使用的因子、持有时间与失效条件，提示点击“生成策略方案”。'''


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    content: str = Field(max_length=16000)


class DiscussionPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = ''
    account_id: str = ''
    messages: list[ChatMessage] = Field(default_factory=list, max_length=80)


class StrategyPlan(ResearchSpec):
    hypothesis: str = Field(min_length=1, max_length=5000)


def _context() -> dict:
    from portfolio.accounts import AccountRegistry
    factors = [dict(name=f.name, display_name=f.display_name or f.name, assumption=f.assumption,
                    direction=f.direction, status=f.status.value) for f in FactorRegistry().list()
               if f.status in {FactorStatus.ACTIVE, FactorStatus.DRAFT} and f.direction in {'positive', 'negative'}]
    return {'factors': factors, 'defaults': defaults(), 'local_models': METHODS,
            'accounts': [{'account_id': a.account_id, 'alias': a.alias, 'star_market_enabled': a.star_market_enabled}
                         for a in AccountRegistry().list(market='CN-A', instrument_class='stock')]}


def _conversation(payload: DiscussionPayload):
    return [{'role': m.role, 'content': m.content.strip()} for m in payload.messages if m.content.strip()]


@router.get('/strategies/research/models')
def research_models():
    matrix = load_matrix()
    configured = set(secrets.list_secret_names())
    items = []
    for model in ModelRegistry().list(kind=ModelKind.API_MODEL):
        score = score_model_for_task(matrix, model, 'structured_config')
        if score < 0 or not model.enabled:
            continue
        items.append({'name': model.name, 'score': score, 'selectable': env_var_name(model.name) in configured})
    items.sort(key=lambda row: (-row['score'], row['name']))
    recommended = next((row['name'] for row in items if row['selectable']), '')
    return {'recommended': recommended, 'items': [{**row, 'recommended': row['name'] == recommended} for row in items]}


@router.post('/strategies/research/chat')
def discuss_strategy(payload: DiscussionPayload):
    ai = AIRouter()
    model = ai.pick('structured_config', model_name=payload.model.strip() or None)
    context = _context()
    if payload.account_id and payload.account_id not in {a['account_id'] for a in context['accounts']}:
        raise ContractViolation(code='STRATEGY_ACCOUNT_INVALID', message='所选账户不可用，请重新选择。')
    context['selected_account_id'] = payload.account_id
    messages = [{'role': 'system', 'content': _DISCUSSION_SYSTEM + '\n可用研究条件：\n' + json.dumps(context, ensure_ascii=False)}]
    return chat_stream(ai, model, messages + _conversation(payload))


def _generate_plan(ai, selected, payload, context, report) -> StrategyPlan:
    system = '''根据策略讨论生成一个 StructuredConfig JSON 对象，config_kind 固定为 strategy_research_plan，
proposed_value 必须符合下方 StrategyPlan schema。hypothesis 用中文说明策略逻辑、因子互补与失效条件。
factors 只能从可用因子中选取，至少两个；model 是本地量化模型，不是讨论所用的语言模型。
account_id 必须使用 selected_account_id，不得替用户选其他账户。资金和市场权限由账户读取，不输出 capital 或 star_market_enabled。
除非用户明确指定，否则使用给定默认日期、model=auto、20只股票、月度调仓。模型在训练诊断后选择。
不得生成因子权重、股票名单或回测指标，它们由后续本地模型和计算引擎产生。
不得输出代码，不得改变系统交易费用或增加审批要求。
StrategyPlan schema：\n''' + json.dumps(StrategyPlan.model_json_schema(), ensure_ascii=False)
    prompt = json.dumps({'discussion': _conversation(payload), 'available': context}, ensure_ascii=False)
    last_error = ''
    allowed = {f['name'] for f in context['factors']}
    for attempt in range(1, 4):
        report.progress({'state': 'running', 'step': f'整理并检查策略方案（{attempt}/3）', 'percent': 15 + attempt * 15})
        try:
            raw = ai.run_task(task='structured_config', model_name=selected.name, system=system,
                              prompt=prompt + ('\n请修正上次方案的问题：' + last_error if last_error else ''))
            if raw.get('config_kind') != 'strategy_research_plan':
                raise ContractViolation(code='STRATEGY_AI_PLAN_KIND', message='策略方案格式不正确。')
            plan = StrategyPlan.model_validate(raw.get('proposed_value'))
            if plan.account_id != payload.account_id:
                raise ContractViolation(code='STRATEGY_ACCOUNT_MISMATCH', message='AI 不得更换用户选择的账户。')
            if not set(plan.factors).issubset(allowed):
                raise ContractViolation(code='STRATEGY_AI_FACTOR_UNKNOWN', message='策略方案引用了不可用的因子。')
            if plan.end > date.fromisoformat(context['defaults']['end']):
                raise ContractViolation(code='STRATEGY_AI_FUTURE_WINDOW', message='回测结束日期超出了已发布数据范围。')
            return plan
        except (ValidationError, ContractViolation, ModelRouteError) as exc:
            if isinstance(exc, ModelRouteError) and exc.code not in {'AI_OUTPUT_NOT_JSON', 'AI_OUTPUT_NOT_OBJECT', 'AI_OUTPUT_EMPTY'}:
                raise
            last_error = str(exc)
            report(f'策略方案第 {attempt} 次检查未通过：{last_error}')
    raise ContractViolation(code='STRATEGY_AI_PLAN_INVALID', message='三次尝试后仍未得到可执行的策略方案。',
                            context={'last_error': last_error}, suggested_action='保留当前讨论，检查研究模型与可用因子后重试。')


@router.post('/strategies/research/start')
def start_idea_research(payload: DiscussionPayload):
    if not any(m.role == 'user' and m.content.strip() for m in payload.messages):
        raise ContractViolation(code='STRATEGY_AI_DISCUSSION_EMPTY', message='请先填写研究想法。')
    context = _context()
    if not payload.account_id or payload.account_id not in {a['account_id'] for a in context['accounts']}:
        raise ContractViolation(code='STRATEGY_ACCOUNT_REQUIRED', message='请先选择用于生成策略的账户；创建后不可更换。')
    context['selected_account_id'] = payload.account_id
    if len(context['factors']) < 2:
        raise ContractViolation(code='STRATEGY_FACTOR_UNAVAILABLE', message='至少需要两个可研究且方向明确的因子，才能开始策略实验。')
    if not context['defaults']:
        raise ContractViolation(code='STRATEGY_DATA_UNAVAILABLE', message='尚无可用的研究数据区间，请先完成数据同步。')
    ai = AIRouter()
    selected = ai.pick('structured_config', model_name=payload.model.strip() or None)

    def run(report):
        report.progress({'state': 'running', 'step': '整理策略研究想法', 'percent': 8})
        plan = _generate_plan(ai, selected, payload, context, report)
        spec = ResearchSpec.model_validate(plan.model_dump(exclude={'hypothesis'}))
        doc = create_strategy(spec, hypothesis=plan.hypothesis, discussion_model=selected.name)
        report('策略假设、因子组合和本地模型已登记；数值权重由本地模型训练。')
        report.progress({'state': 'running', 'step': '策略方案已就绪，等待启动实验', 'percent': 100})
        return {'profile_name': doc.profile_name, 'display_name': doc.display_name,
                'hypothesis': plan.hypothesis, 'approval_status': 'pending', 'model': selected.name,
                'account_id': plan.account_id, 'account_snapshot': doc.extra['creation_account_snapshot']}

    task_id = manager.start('新策略自动研究', run, resource='strategy-ai-research', category='research')
    return {'task_id': task_id, 'model': selected.name}


@router.post('/strategies/research/{task_id}/approve')
def approve_idea_research(task_id: str):
    research_progress(task_id)

    def start(result):
        started = start_research(result['profile_name'])
        return {'experiment_id': started['task_id']}

    return json_safe(manager.approve_result(task_id, resource='strategy-ai-research', start=start))


@router.get('/strategies/research/{task_id}/progress')
def research_progress(task_id: str):
    task = manager.get(task_id)
    if task.get('resource') != 'strategy-ai-research':
        raise HTTPException(404, '未找到策略方案生成任务')
    return json_safe({key: task.get(key) for key in ('task_id', 'status', 'progress', 'log', 'error', 'result')})


@router.get('/strategies/experiments')
def experiment_sessions():
    docs = {doc.profile_name: doc for doc in StrategyProfileStore().list() if is_research(doc)}
    with manager._lock:
        tasks = [dict(task) for task in manager._tasks.values() if str(task.get('resource', '')).startswith('strategy-research:')]
    items = []
    for task in sorted(tasks, key=lambda row: row['created_at'], reverse=True):
        name = task['resource'].partition(':')[2]
        if name not in docs:
            continue
        doc = docs[name]
        items.append({'experiment_id': task['task_id'], 'profile_name': name, 'display_name': doc.display_name,
                      'model': doc.extra['research_spec']['model'], 'research_model': doc.extra.get('discussion_model', ''),
                      'started_at': task['created_at'], 'state': task['status'], 'phase': task['progress'].get('step', '')})
    return {'items': items}


@router.get('/strategies/experiments/{task_id}')
def experiment_detail(task_id: str):
    from presentation import factor_display_name
    task = manager.get(task_id)
    resource = str(task.get('resource') or '')
    if not resource.startswith('strategy-research:'):
        raise HTTPException(404, '未找到策略实验')
    doc = require_strategy(resource.partition(':')[2])
    result = task.get('result') if task['status'] == 'completed' else None
    return json_safe({'experiment_id': task_id, 'profile_name': doc.profile_name, 'display_name': doc.display_name,
        'status': doc.status, 'hypothesis': doc.extra.get('research_hypothesis', ''),
        'spec': (result or {}).get('spec') or doc.extra['research_spec'], 'task': task, 'result': result,
        'factor_display_names': {ref.name: factor_display_name(ref.name) for ref in doc.factors.items}})
