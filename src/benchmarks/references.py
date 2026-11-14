"""Store display references separately from immutable backtest results."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ai.router import AIRouter
from exceptions import ContractViolation
from paths import workspace
from .catalogue import catalogue, catalogue_hash
from .models import MarketReference
from .selection import available_indices


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _path(run_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ContractViolation(code='MARKET_REFERENCE_RUN_INVALID', message='回测编号格式无效。')
    return workspace().runs / 'market_references' / f'{run_id}.json'


def _save(path: Path, value: dict):
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))
    pending.replace(path)


def _check(reference: MarketReference) -> MarketReference:
    matches = [row for row in catalogue() if row['index_code'] == reference.code]
    if len(matches) != 1 or not matches[0]['market_eligible']:
        raise ContractViolation(code='BENCHMARK_MARKET_SCOPE_INVALID', message='市场参照不在 CSMAR 可用宽基目录中。')
    return reference


def reference_for_run(run_id: str, saved: dict) -> MarketReference:
    plan = ((saved.get('profile') or {}).get('extra') or {}).get('benchmark_plan')
    if plan:
        return _check(MarketReference(
            code=plan['market_reference'], rationale=plan['rationale'], selection_timing='before_backtest',
            selection_model=plan.get('selection_model', ''), selected_at=plan.get('selected_at', ''),
            catalogue_hash=plan.get('catalogue_hash', ''), selection_window=plan.get('selection_window', {})))
    path = _path(run_id)
    if not path.exists():
        raise ContractViolation(code='MARKET_REFERENCE_REQUIRED', message='此回测尚未配置展示用市场参照。')
    raw = json.loads(path.read_text())
    if raw.get('config_hash') != _digest(saved):
        raise ContractViolation(code='MARKET_REFERENCE_CONFIG_CHANGED', message='市场参照配置与回测记录不一致。')
    return _check(MarketReference.model_validate(raw['reference']))


def select_display_reference(scope: dict, start: str, end: str, *, model_name=None) -> MarketReference:
    records = [row for row in available_indices(start, end) if row['market_eligible']]
    if not records:
        raise ContractViolation(code='BENCHMARK_MARKET_DATA_INCOMPLETE', message='没有覆盖整个区间的 CSMAR 宽基指数。')
    # Deliberately exclude NAV, returns, factors, scores and candidate results.
    context = {'scope': scope, 'start': start, 'end': end,
               'candidates': [{key: row[key] for key in ['index_code', 'name', 'coverage']} for row in records]}
    router = AIRouter()
    selected_model = router.pick('market_reference_selection', model_name=model_name)
    output = router.run_task(task='market_reference_selection', model_name=selected_model.name,
        system='按投资范围选择整体股票市场参照，仅用于已有回测的图表展示。'
               '不修改收益基准或评价结果，不依据业绩选择。只能从candidates选择一项宽基指数。'
               '输出JSON字段code（字符串代码）、rationale、task=market_reference_selection、model_name。',
        prompt=json.dumps(context, ensure_ascii=False, default=str))
    if output['code'] not in {row['index_code'] for row in records}:
        raise ContractViolation(code='BENCHMARK_MARKET_SCOPE_INVALID', message='模型所选市场参照不在已覆盖的宽基候选中。')
    return MarketReference(code=output['code'], rationale=output['rationale'],
        selection_timing='after_backtest_display', selection_model=selected_model.name,
        catalogue_hash=catalogue_hash(), selection_window={'start': start, 'end': end})


def configure_display_references(rows: list[dict], *, model_name=None) -> dict:
    """Explicit maintenance step; never call an LLM from the read-only chart API."""
    groups = {}
    already_configured = 0
    for row in rows:
        saved = json.loads(row['config'])
        run_id = row['backtest_run']
        if ((saved.get('profile') or {}).get('extra') or {}).get('benchmark_plan') or _path(run_id).exists():
            reference_for_run(run_id, saved)
            already_configured += 1
            continue
        profile = saved['profile']
        scope = {key: profile.get(key) for key in ['market', 'instruments', 'universe']}
        group = groups.setdefault(_digest(scope), {'scope': scope, 'runs': []})
        group['runs'].append((run_id, saved))
    updated = []
    choices = []
    for group in groups.values():
        start = min(saved['config']['start'] for _, saved in group['runs'])
        end = max(saved['config']['end'] for _, saved in group['runs'])
        identity = {'scope': group['scope'], 'start': start, 'end': end}
        choice_path = workspace().runs / 'market_reference_choices' / f'{_digest(identity)}.json'
        if choice_path.exists():
            reference = _check(MarketReference.model_validate(json.loads(choice_path.read_text())))
        else:
            reference = select_display_reference(group['scope'], start, end, model_name=model_name)
            _save(choice_path, reference.model_dump(mode='json'))
        choices.append(reference.model_dump(mode='json'))
        for run_id, saved in group['runs']:
            _save(_path(run_id), {'config_hash': _digest(saved), 'reference': reference.model_dump(mode='json')})
            updated.append(run_id)
    return {'updated_runs': updated, 'already_configured': already_configured, 'choices': choices}
