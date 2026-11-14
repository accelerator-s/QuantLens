import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import pandas as pd
from lifecycle.factor_research import FactorResearchIndex
from factors.registry import hash_code

@pytest.mark.parametrize('published', [False, True])
def test_similarity_requires_exact_candidate_definition(tmp_path, monkeypatch, published):
    source='def compute(panel): return panel["close"].shift(1)'
    definition={'code':source,'parameters':{},'dependencies':['close'],'standardization':['zscore'],'missing_handling':'drop','direction':'positive'}
    (tmp_path/'version.json').write_text(json.dumps(definition))
    monkeypatch.setattr('lifecycle.factor_research.workspace',lambda:SimpleNamespace(root=tmp_path))
    baseline=SimpleNamespace(name='probe',version='v1',code_hash=hash_code('different formula'),**{k:v for k,v in definition.items() if k!='code'})
    runtime=SimpleNamespace(name='probe_r_123456789abc',version='candidate-v2',code_hash=hash_code(source),**{k:v for k,v in definition.items() if k!='code'})
    monkeypatch.setattr('factors.registry.FactorRegistry',lambda:SimpleNamespace(list=lambda **kwargs:[baseline]+([runtime] if published else [])))
    matrix=Mock(return_value=pd.DataFrame([[1.,2.,3.],[2.,1.,3.]]))
    monkeypatch.setattr('quantlens.research_data.factor_matrix',matrix)
    store=Mock();store.get.return_value=SimpleNamespace(validation_window=SimpleNamespace(start='2022-01-01',end='2024-12-31'))
    record=SimpleNamespace(factor_name='probe',research_program_id='RP-test',factor_version_id='FV-test',code_ref='version.json')
    result=FactorResearchIndex(store)._signal_similarity(record,record)
    if published:
        assert result['signal_correlation_status']=='completed'
        assert all(call.args[:2]==('probe_r_123456789abc','candidate-v2') for call in matrix.call_args_list)
    else:
        matrix.assert_not_called()
        assert result['signal_correlation_status']=='failed'
        assert 'FACTOR_SIGNAL_VERSION_NOT_PUBLISHED' in result['signal_correlation_error']
