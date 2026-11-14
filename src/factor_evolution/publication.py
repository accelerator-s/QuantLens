"""Apply an accepted research result to the original catalog entry."""
from __future__ import annotations

import fcntl
import json
import re

from exceptions import QuantTrainingError
from factors.plugins import FactorPlugin
from factors.registry import FactorMetadata, FactorRegistry, FactorStatus, hash_code

from .store import EvolutionStore


def capture_catalog_base(store: EvolutionStore, research_id: str) -> None:
    path = store.run_dir(research_id) / 'catalog_base.json'
    if path.exists():
        return
    meta = FactorRegistry().get(store.load_config(research_id).base_factor)
    store._write_json(path, {'metadata': meta.model_dump(mode='json'),
                             'code': FactorPlugin.default().read(meta.name)})


def apply_accepted_version(store: EvolutionStore, research_id: str) -> dict | None:
    detail = store.detail(research_id)
    status = detail['status']
    if status['state'] != 'completed' or status['decision'] != 'frozen':
        return None
    version_id = status['best_version_id']
    tests = [e['payload'] for e in detail['events'] if e['event_type'] == 'locked_test_completed'
             and e['payload'].get('version_id') == version_id]
    if not tests or not tests[-1].get('passed'):
        return None
    version = store.load_version(research_id, version_id)
    name = detail['config']['base_factor']
    if version.factor_name != name:
        raise QuantTrainingError(code='EVOLUTION_FACTOR_ID_CHANGED', message='迭代结果必须沿用原因子标识。')
    registry = FactorRegistry()
    plugin = FactorPlugin.default()
    baseline_path = store.run_dir(research_id) / 'catalog_base.json'
    if not baseline_path.exists():
        raise QuantTrainingError(code='EVOLUTION_CATALOG_BASE_MISSING', message='缺少训练开始时的因子定义，无法写回。')
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    baseline['metadata'] = FactorMetadata.model_validate(baseline['metadata']).model_dump(mode='json')
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    with (registry.path.parent / '.factor-publication.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = registry.get(name)
        plan_path = store.run_dir(research_id) / 'catalog_publication.json'
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
        else:
            if current.model_dump(mode='json') != baseline['metadata'] or plugin.read(name) != baseline['code']:
                raise QuantTrainingError(code='EVOLUTION_CATALOG_BASE_CHANGED',
                                         message='训练期间原因子已更新，本次结果不能覆盖较新的版本。')
            number = re.match(r'^v(\d+)(?:$|[-_.])', current.version)
            next_version = f'v{int(number.group(1)) + 1}' if number else current.version + '.1'
            updated = current.model_copy(update={
                'version': next_version, 'assumption': version.assumption,
                'code_hash': hash_code(version.code), 'parameters': dict(version.parameters),
                'dependencies': list(version.dependencies), 'direction': version.direction,
                'timescale': version.timescale, 'standardization': list(version.standardization),
                'missing_handling': version.missing_handling, 'status': FactorStatus.DRAFT,
                'last_evaluated_at': None,
            })
            plan = {'factor_name': name, 'from_version': current.version, 'to_version': next_version,
                    'research_version_id': version_id, 'metadata': updated.model_dump(mode='json')}
            store._write_json(plan_path, plan)
        target = FactorMetadata.model_validate(plan['metadata'])
        # A retry after a completed write must not increment the version again.
        comparable = lambda meta: meta.model_dump(mode='json', exclude={'updated_at'})
        if comparable(current) == comparable(target) and plugin.read(name) == version.code:
            return {key: plan[key] for key in ('factor_name', 'from_version', 'to_version', 'research_version_id')}
        if current.model_dump(mode='json') != baseline['metadata'] or plugin.read(name) not in (baseline['code'], version.code):
            raise QuantTrainingError(code='EVOLUTION_CATALOG_BASE_CHANGED',
                                     message='原因子已有其他更新，本次结果未写回。')
        previous_source = plugin.read(name)
        previous_registry = registry.path.read_bytes()
        try:
            plugin.write(name, version.code, override=True)
            registry.upsert(target, override=True)
        except Exception:
            plugin.write(name, previous_source, override=True)
            registry.path.write_bytes(previous_registry)
            raise
        result = {key: plan[key] for key in ('factor_name', 'from_version', 'to_version', 'research_version_id')}
        store.append_event(research_id, 'catalog_version_updated', result)
        return result
