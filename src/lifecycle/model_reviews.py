"""Persist model opinions separately from computed metrics and admission gates."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from exceptions import ContractViolation
from paths import workspace
from security import confined_path


class ReviewOpinion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conclusion: str = Field(min_length=1, max_length=6000)
    risks: list[str] = Field(default_factory=list, max_length=10)
    next_steps: list[str] = Field(default_factory=list, max_length=10)

    @field_validator('conclusion')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('评价结论不能为空')
        return value.strip()


def _directory(kind, name):
    if kind not in {'factor', 'strategy'}:
        raise ValueError('Unknown review subject')
    return confined_path(workspace().runs / 'model_reviews' / kind, name, '')


def _factor_sessions(name):
    from factor_evolution.store import EvolutionStore
    store = EvolutionStore()
    return store, [row for row in store.list() if row['base_factor'] == name]


def _research_reviews(kind, name):
    """Expose genuine saved model output, retaining its original evidence scope."""
    if kind == 'factor':
        store, sessions = _factor_sessions(name)
        for session in sessions:
            detail = store.detail(session['research_id'])
            for index, event in enumerate(detail['events']):
                payload = event['payload']
                review = payload.get('review') or {}
                if event['event_type'] != 'round_completed' or not review.get('ai_generated', True):
                    continue
                if not review.get('assessment'):
                    continue
                yield {'review_id': f"{session['research_id']}-{index}",
                       'created_at': event['created_at'], 'model_name': review.get('model_name', ''),
                       'scope': '训练期候选复盘', 'source_id': session['research_id'],
                       'version': payload.get('version_id', ''), 'conclusion': review['assessment'],
                       'risks': [review['failure_reason']] if review.get('failure_reason') else [],
                       'next_steps': [review['next_change']] if review.get('next_change') else []}
    else:
        from strategy.research import result_path, read_result
        # Each study retains the result of that research, even after a rerun.
        root = result_path(name).parent / 'studies'
        results = []
        current = read_result(name)
        if current:
            results.append((current, result_path(name)))
        for path in root.glob('*/result.json'):
            result = json.loads(path.read_text(encoding='utf-8'))
            if result.get('profile_name') != name:
                continue
            if current and result.get('run_id') == current.get('run_id'):
                continue
            results.append((result, path))
        for result, path in results:
            actions = [action for dialogue in result.get('search', {}).get('dialogues', [])
                       for action in dialogue if action.get('action') != 'query']
            if not actions:
                continue
            action = actions[-1]
            conclusion = action.get('stop_reason') or action.get('observation')
            if conclusion:
                yield {'review_id': f"{result['run_id']}-diagnosis",
                       'created_at': result.get('completed_at') or datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                       'model_name': result.get('research_model', ''),
                       'scope': '训练期选模诊断', 'source_id': result['run_id'],
                       'version': result.get('freeze_hash', ''), 'conclusion': conclusion,
                       'risks': [action['alternative']] if action.get('alternative') else [],
                       'next_steps': [action['falsification']] if action.get('falsification') else []}


def list_reviews(kind, name):
    records = list(_research_reviews(kind, name))
    for path in _directory(kind, name).glob('REV-*.json'):
        records.append(json.loads(path.read_text(encoding='utf-8')))
    return sorted(records, key=lambda item: (item.get('created_at', ''), item['review_id']), reverse=True)


def review_evidence(kind, name):
    if kind == 'strategy':
        from strategy.research import require_strategy, read_result
        doc = require_strategy(name)
        result = read_result(name)
        if not result or result.get('state') != 'completed':
            raise ContractViolation(code='MODEL_REVIEW_RESULTS_REQUIRED', message='请先完成策略回测，再生成评价。')
        evidence = {key: result.get(key) for key in ('run_id', 'freeze_hash', 'spec', 'account_snapshot',
                    'selected_candidate', 'accepted', 'metrics', 'final_evaluation', 'final_baseline')}
        evidence.update(hypothesis=doc.extra.get('research_hypothesis', ''),
                        scope='冻结后最终回测评价', subject=name)
        return evidence, doc.extra.get('discussion_model') or None

    from factors.registry import FactorRegistry
    from quantlens import ads
    from quantlens.evaluation_view import view
    factor = FactorRegistry().get(name)
    rows = ads.query('factor_eval', 'factor=? AND version=?', [name, factor.version],
                     limit=100, order='as_of DESC,eval_end DESC,horizon')
    if not rows:
        raise ContractViolation(code='MODEL_REVIEW_RESULTS_REQUIRED', message='请先完成并发布当前因子版本的评价，再生成模型评价。')
    latest = rows[0]
    rows = [row for row in rows if (row['run_id'], row['eval_start'], row['eval_end']) ==
            (latest['run_id'], latest['eval_start'], latest['eval_end'])]
    primary = next((row for row in rows if row['horizon'] == 5), rows[0])
    daily = ads.query('factor_eval_daily', 'factor=? AND version=? AND run_id=? AND horizon=?',
                      [name, factor.version, primary['run_id'], primary['horizon']], limit=10000, order='date')
    daily = [row for row in daily if str(latest['eval_start']) <= str(row['date']) <= str(latest['eval_end'])]
    _, sessions = _factor_sessions(name)
    evidence = {'subject': name, 'version': factor.version, 'code_hash': factor.code_hash,
                'data_snapshot': latest.get('data_snapshot'), 'as_of': latest.get('as_of'),
                'hypothesis': factor.assumption, 'run_id': primary['run_id'],
                'scope': '已发布因子评价', 'metrics': view(rows, daily, primary['horizon'], factor.direction),
                'limitations': '分组收益为毛收益，未扣交易费用；不等同于可实施策略的净收益。'}
    return evidence, (sessions[0].get('model_name') if sessions else None)


def generate_review(kind, name, *, evidence=None, model_name=None, router=None):
    from ai.router import AIRouter
    from ai.registry import ModelRegistry
    directory = _directory(kind, name)
    if evidence is None:
        evidence, preferred = review_evidence(kind, name)
        model_name = model_name or preferred
    router = router or AIRouter()
    selected = router.pick('structured_config', model_name=model_name or ModelRegistry().get_discuss_model() or None)
    raw = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)
    payload = router.run_task(task='structured_config', model_name=selected.name,
        system='你负责依据已完成的量化证据评价因子或策略。只返回 StructuredConfig，'
               'config_kind=model_review，proposed_value 包含 conclusion（中文结论）、'
               'risks（风险字符串列表）、next_steps（后续建议字符串列表）。'
               '说明证据是否支持核心假设、收益与回撤表现及适用限制。不得补造指标、'
               '将毛收益说成净收益、将训练期说成最终测试，或将准入条件说成模型决定。'
               '评价只供记录，不得修改参数、准入条件或启停状态。',
        prompt=json.dumps({'evidence': json.loads(raw), 'schema': ReviewOpinion.model_json_schema()}, ensure_ascii=False))
    if payload.get('config_kind') != 'model_review':
        raise ContractViolation(code='MODEL_REVIEW_INVALID', message='模型未返回有效的评价记录。')
    opinion = ReviewOpinion.model_validate(payload.get('proposed_value') or {})
    record = {'review_id': 'REV-' + uuid.uuid4().hex, 'created_at': datetime.now(UTC).isoformat(),
              'model_name': selected.name, 'scope': evidence['scope'],
              'source_id': evidence['run_id'], 'version': evidence.get('version') or evidence.get('freeze_hash', ''),
              'evidence_hash': hashlib.sha256(raw.encode()).hexdigest(),
              'evidence': json.loads(raw), **opinion.model_dump()}
    directory.mkdir(parents=True, exist_ok=True)
    path = confined_path(directory, record['review_id'], '.json')
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    pending.replace(path)
    return record
