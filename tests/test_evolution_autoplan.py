from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from exceptions import QuantTrainingError
from factor_evolution import autoplan
from strategy.models import FactorRef, FactorSpec, StrategyProfileDoc, StrategyStatus, WeightMode
from strategy.store import StrategyProfileStore


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    store = StrategyProfileStore(path=tmp_path / 'strategies')
    template = Path(__file__).resolve().parents[1] / 'config/strategies/a_share_research_base.yaml'
    store.upsert(StrategyProfileDoc.model_validate(yaml.safe_load(template.read_text())))
    monkeypatch.setattr(autoplan, 'StrategyProfileStore', lambda: store)
    monkeypatch.setattr(autoplan, 'FactorRegistry', lambda: SimpleNamespace(get=lambda _: SimpleNamespace(
        name='probe', display_name='测试因子', status='draft', timescale='daily',
        applicable_markets=['CN-A'], applicable_instruments=['stock'])))
    return store


def test_factor_selection_automatically_creates_a_single_factor_research_profile(profiles):
    name, embargo = autoplan._single_factor_profile('probe')
    resolved = profiles.resolve(name)
    assert name == 'research_probe' and embargo == resolved.holding_period
    assert [ref.name for ref in resolved.factors.items] == ['probe']
    assert resolved.extra['internal_only']
    assert resolved.execution_assumption.price_anchor == 'next_close'
    assert autoplan._single_factor_profile('probe') == (name, embargo)


@pytest.mark.parametrize('status,factor', [('disabled', 'probe'), ('draft', 'other')])
def test_conflicting_research_profile_is_rejected_without_overwriting(profiles, status, factor):
    profiles.upsert(StrategyProfileDoc(profile_name='research_probe', inheritance_parent='a_share_research_base',
        status=StrategyStatus(status), factors=FactorSpec(weighting=WeightMode.EQUAL, items=[FactorRef(name=factor)])))
    before = profiles.get('research_probe').model_dump()
    with pytest.raises(QuantTrainingError, match='已停用或未严格引用'):
        autoplan._single_factor_profile('probe')
    assert profiles.get('research_probe').model_dump() == before


def test_disabled_factor_stops_before_snapshot_or_profile_creation(profiles, monkeypatch):
    monkeypatch.setattr(autoplan, 'FactorRegistry', lambda: SimpleNamespace(get=lambda _: SimpleNamespace(status='disabled')))
    monkeypatch.setattr(autoplan, 'cache_status', lambda: pytest.fail('Disabled factors must fail preflight'))
    with pytest.raises(QuantTrainingError) as error:
        autoplan.build_auto_config('probe')
    assert error.value.code == 'EVOLUTION_AUTO_FACTOR_UNAVAILABLE'
    assert not any(doc.profile_name == 'research_probe' for doc in profiles.list())
