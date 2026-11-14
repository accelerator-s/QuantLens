"""Bounded strategy search: diagnose inner folds, freeze, evaluate outer once."""
from __future__ import annotations

import json
import time
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from exceptions import ContractViolation
from factor_evolution.analysis import Citation
from factor_evolution.diagnostics import EvidenceQueries, digest

METHODS = {
    'equal': '等权基线，无拟合参数',
    'icir': '按训练期每日秩 IC 的均值和波动配权',
    'model_linear': '截面去均值线性回归',
    'model_ridge': '固定 L2 收缩的线性回归，缓解相关因子的权重不稳',
    'model_logistic': '预测跑赢截面中位数的概率排序',
}


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    method: Literal['equal', 'icir', 'model_linear', 'model_ridge', 'model_logistic']
    max_positions: int = Field(ge=1, le=100)
    frequency: Literal['weekly', 'monthly', 'quarterly']


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['query', 'candidate', 'stop']
    section: Literal['overview', 'annual', 'costs', 'drawdown', 'signal', 'limitations'] | None = None
    candidate: Candidate | None = None
    observation: str = ''
    hypothesis: str = ''
    alternative: str = ''
    falsification: str = ''
    evidence_refs: list[Citation] = Field(default_factory=list)
    stop_reason: str = ''

    @model_validator(mode='after')
    def complete(self):
        if self.action == 'query' and self.section is None:
            raise ValueError('查询必须指定证据分区')
        if self.action == 'candidate' and (self.candidate is None or not self.evidence_refs or
                not all(v.strip() for v in [self.observation, self.hypothesis, self.alternative, self.falsification])):
            raise ValueError('候选必须有证据、机制、替代解释和可反证条件')
        if self.action == 'stop' and not self.stop_reason.strip():
            raise ValueError('停止必须说明原因')
        return self


def fold_windows(dates):
    """Three expanding folds entirely inside training, with maturity purging later."""
    days = sorted(set(str(d)[:10] for d in dates))
    if len(days) < 240:
        raise ValueError('策略内层研究至少需要 240 个交易日')
    blocks = np.array_split(days[len(days)//2:], 3)
    return [{'fit_start': days[0], 'fit_end': days[days.index(str(block[0]))-1],
             'start': str(block[0]), 'end': str(block[-1])} for block in blocks]


def beats(candidate, baseline):
    """Measured noninferiority on every inner fold; no risk/return substitution."""
    if candidate['comparison_hash'] != baseline['comparison_hash']:
        raise ValueError('策略比较基准、数据、账户或风险约束发生变化')
    if len(candidate['folds']) != len(baseline['folds']):
        raise ValueError('策略内层窗口不一致')
    better = False
    for c, b in zip(candidate['folds'], baseline['folds']):
        if c['window'] != b['window']:
            raise ValueError('策略内层窗口不一致')
        cr, br = c['metrics']['returns']['annualized_return'], b['metrics']['returns']['annualized_return']
        cd, bd = abs(c['metrics']['risk']['max_drawdown']), abs(b['metrics']['risk']['max_drawdown'])
        if cr < br-1e-6 or cd > bd+1e-6 or not c['metrics']['objective']['eligible']:
            return False
        better |= cr > br+1e-6 or cd < bd-1e-6
    return better


def evidence_for(results, mandate):
    sections = {k: {} for k in ['overview', 'annual', 'costs', 'drawdown', 'signal']}
    for i, result in enumerate(results):
        key = f'candidate_{i}'
        sections['overview'][key] = {'candidate': result['candidate'], 'folds': [
            {'window': f['window'], 'returns': f['metrics']['returns'], 'risk': f['metrics']['risk'],
             'execution': f['diagnostics']['overview']} for f in result['folds']]}
        for k in ['annual', 'costs', 'drawdown', 'signal']:
            sections[k][key] = [f['diagnostics'][k] for f in result['folds']]
    sections['limitations'] = {
        'mandate': mandate, 'methods': METHODS,
        'scope': '仅训练区间内的顺序留出折；模型选择使用这些折，不属于最终样本外结果。',
        'capital': '账户快照确定资金；多个策略为同一账户的独立方案，不代表资金可重复投入。',
        'weights': '本地算法拟合因子信号系数，不是股票仓位；AI 只能选择方法，不得生成系数。',
        'causality': '相关性和归因不能单独证明因果；费用加回不是无费用反事实。',
    }
    return {'run_id': mandate['research_id'], 'definition_hash': digest([r['candidate'] for r in results]),
            'comparison_hash': results[0]['comparison_hash'], 'sections': sections}


def choose_action(results, mandate, responder, *, max_turns=6, timeout=300):
    queries = EvidenceQueries(evidence_for(results, mandate))
    packets = [queries.query(k) for k in ['overview', 'costs', 'drawdown', 'signal', 'limitations']]
    dialogue = []
    deadline = time.monotonic()+timeout
    for turn in range(max_turns):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('策略证据分析超时')
        raw = responder({'evidence': packets, 'dialogue': dialogue, 'schema': Action.model_json_schema(),
                         'stage': 'strategy_inner_training_only', 'turns_remaining': max_turns-turn}, remaining)
        if time.monotonic() > deadline:
            raise TimeoutError('策略证据分析超时')
        action = Action.model_validate(raw)
        dialogue.append(action.model_dump(mode='json'))
        if action.action == 'query':
            packet = queries.query(action.section)
            if not any(p['evidence_id'] == packet['evidence_id'] for p in packets):
                packets.append(packet)
            continue
        if action.action == 'candidate':
            queries.verify(action.evidence_refs)
        return action, dialogue
    raise ValueError('策略分析达到轮数上限，没有可执行结论')


def configured_responder(model_name=None):
    from ai.router import AIRouter
    ai = AIRouter()
    selected = ai.pick('structured_config', model_name=model_name)
    def respond(context, remaining):
        router = AIRouter(request_timeout=max(1., min(180., remaining)))
        raw = router.run_task(task='structured_config', model_name=selected.name,
            system='你监督策略训练。先解释内层基线收益、亏损、费用和资金可实施性，再提出一个可反证候选。'
                   '可多轮查询 evidence 中的六类证据。只修改配权方法、持仓数量、调仓频率中的一个轴。'
                   '全部候选相对 candidate_0，不放宽风控、不修改资金、因子版本或基准。'
                   '等权也是合法方案；复杂模型必须用实际扣费结果证明增益。不得要求人工审批。'
                   '返回 StructuredConfig，config_kind=strategy_training_action，proposed_value 符合 schema。',
            prompt=json.dumps(context, ensure_ascii=False))
        if raw.get('config_kind') != 'strategy_training_action':
            raise ContractViolation(code='STRATEGY_ANALYSIS_KIND', message='策略分析返回了错误的数据类型。')
        return raw['proposed_value']
    respond.model_name = selected.name
    return respond


def search(spec, mandate, evaluate, responder, report=lambda _: None):
    """evaluate receives only inner windows until this function returns a frozen winner."""
    baseline = Candidate(method='equal', max_positions=spec.top_n, frequency=spec.rebalance_frequency)
    report('运行等权组合的训练内分段基线，核算账户资金、整手与最低佣金')
    results = [evaluate(baseline)]
    dialogues = []
    winner = 0
    for iteration in range(spec.max_candidates):
        report(f'策略诊断与候选选择 {iteration+1}/{spec.max_candidates}；最终评估不可见')
        action, dialogue = choose_action(results, mandate, responder)
        dialogues.append(dialogue)
        if action.action == 'stop':
            break
        candidate = action.candidate
        changed = sum(candidate.model_dump()[k] != baseline.model_dump()[k] for k in Candidate.model_fields)
        if changed != 1 or candidate.max_positions > spec.top_n:
            raise ValueError('策略候选须相对等权基线仅修改一个轴，且不能超过持仓数量上限')
        if spec.model != 'auto' and candidate.method not in {'equal', spec.model}:
            raise ValueError('候选模型超出已指定的研究范围')
        if any(r['candidate'] == candidate.model_dump(mode='json') for r in results):
            raise ValueError('策略候选重复，不重复试验')
        result = evaluate(candidate)
        result['registered_hypothesis'] = action.model_dump(mode='json')
        results.append(result)
        if beats(result, results[winner]):
            winner = len(results)-1
    frozen = {'winner': winner, 'candidate': results[winner]['candidate'], 'results': results,
              'dialogues': dialogues, 'comparison_hash': results[0]['comparison_hash'],
              'selection_scope': 'inner_training_only', 'mandate': mandate}
    frozen['freeze_hash'] = digest(frozen)
    return frozen
