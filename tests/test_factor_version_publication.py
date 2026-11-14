import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from exceptions import QuantTrainingError
from factor_evolution.models import EvolutionConfig, EvolutionDecision, EvolutionState, FactorVersion
from factor_evolution.publication import apply_accepted_version, capture_catalog_base
from factor_evolution.store import EvolutionStore
from factors.plugins import FactorPlugin
from factors.registry import FactorMetadata, FactorRegistry


@pytest.fixture
def research(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    registry = FactorRegistry()
    source = 'def compute(p): return p["close"]'
    FactorPlugin.default().write('probe', source)
    registry.upsert(FactorMetadata(name='probe', display_name='60日温和上涨效率',
                                   plugin_path='factors/probe.py', parameters={'window': 60}))
    config = EvolutionConfig(base_factor='probe', profile_name='research_probe',
        train_window={'start': '2018-01-01', 'end': '2021-12-31'},
        validation_window={'start': '2022-01-01', 'end': '2024-12-31'},
        test_window={'start': '2025-01-01', 'end': '2026-01-01'},
        targets={'min_annualized_excess_return': .03, 'max_drawdown': .25,
                 'max_annualized_turnover': 12, 'min_oos_stability': .5})
    store = EvolutionStore()
    status = store.create(config)
    capture_catalog_base(store, status.research_id)
    version = FactorVersion(version_id='v001-candidate', factor_name='probe', assumption='updated',
        direction='negative', timescale='daily', dependencies=['close'], parameters={'window': 60},
        code='def compute(p): return p["close"] * 2', change_type='code', change_summary='更新公式')
    store.save_version(status.research_id, version)
    status.state = EvolutionState.COMPLETED
    status.decision = EvolutionDecision.FROZEN
    status.best_version_id = version.version_id
    store.save_status(status)
    store.append_event(status.research_id, 'locked_test_completed',
                       {'version_id': version.version_id, 'passed': True})
    return store, status, registry, version


def test_accepted_iteration_updates_same_factor_once(research):
    store, status, registry, version = research
    result = apply_accepted_version(store, status.research_id)
    assert result['from_version'] == 'v1' and result['to_version'] == 'v2'
    assert apply_accepted_version(store, status.research_id) == result
    meta, = registry.list(include_research=True)
    assert (meta.name, meta.display_name, meta.version, meta.direction) == (
        'probe', '60日温和上涨效率', 'v2', 'negative')
    assert not meta.production_permission
    assert FactorPlugin.default().read('probe') == version.code


@pytest.mark.parametrize('reason', ['stopped', 'failed_test', 'wrong_version', 'cancelled'])
def test_unaccepted_iteration_keeps_original(research, reason):
    store, status, registry, version = research
    before = registry.path.read_bytes()
    if reason == 'stopped':
        status.decision = EvolutionDecision.STOPPED
    elif reason == 'cancelled':
        status.state = EvolutionState.CANCELLED
    else:
        store.append_event(status.research_id, 'locked_test_completed',
                           {'version_id': version.version_id, 'passed': False})
        if reason == 'wrong_version':
            store.append_event(status.research_id, 'locked_test_completed',
                               {'version_id': 'other', 'passed': True})
    store.save_status(status)
    assert apply_accepted_version(store, status.research_id) is None
    assert registry.path.read_bytes() == before


def test_training_does_not_overwrite_another_update(research):
    store, status, registry, _ = research
    registry.upsert(registry.get('probe').model_copy(update={'version': 'v3'}), override=True)
    with pytest.raises(QuantTrainingError) as error:
        apply_accepted_version(store, status.research_id)
    assert error.value.code == 'EVOLUTION_CATALOG_BASE_CHANGED'
    assert registry.get('probe').version == 'v3'


def test_failed_registry_write_restores_source_and_can_retry(research, monkeypatch):
    store, status, registry, _ = research
    before = FactorPlugin.default().read('probe')
    upsert = FactorRegistry.upsert
    attempts = []

    def fail_once(self, meta, **kwargs):
        attempts.append(meta.version)
        if len(attempts) == 1:
            raise OSError('write failure')
        return upsert(self, meta, **kwargs)

    monkeypatch.setattr(FactorRegistry, 'upsert', fail_once)
    with pytest.raises(OSError):
        apply_accepted_version(store, status.research_id)
    assert registry.get('probe').version == 'v1'
    assert FactorPlugin.default().read('probe') == before
    assert apply_accepted_version(store, status.research_id)['to_version'] == 'v2'
