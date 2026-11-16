import json
from types import SimpleNamespace

import pytest

from lifecycle.experiment_summary import generate_summary


def test_summary_uses_configured_model_and_retains_provenance():
    calls = []
    class Router:
        def pick(self, task, *, model_name):
            assert task == 'structured_config' and model_name == 'configured-model'
            return SimpleNamespace(name=model_name)

        def run_task(self, **kwargs):
            calls.append(kwargs)
            return {'config_kind': 'experiment_summary', 'proposed_value': {
                'conclusion': '训练搜索未获得优于基线的候选，未运行验证和锁定测试。'}}

    evidence = {'training_completed': True, 'validation_completed': False, 'locked_test_completed': False}
    record = generate_summary(evidence, model_name='configured-model', router=Router())
    assert record['ai_generated'] and record['model_name'] == 'configured-model'
    assert len(record['evidence_hash']) == 64
    assert json.loads(calls[0]['prompt'])['evidence'] == evidence
    assert calls[0]['model_name'] == 'configured-model'
    schema = json.loads(calls[0]['prompt'])['schema']
    assert set(schema['required']) == {'config_kind', 'proposed_value'}
    assert schema['properties']['config_kind']['const'] == 'experiment_summary'
    assert schema['properties']['proposed_value']['required'] == ['conclusion']


def test_summary_retries_invalid_envelope_without_changing_evidence():
    from exceptions import ContractViolation
    from unittest.mock import Mock
    run = Mock(side_effect=[
        ContractViolation(code='AI_OUTPUT_SCHEMA_INVALID', message='config_kind missing'),
        {'config_kind': 'experiment_summary', 'proposed_value': {'conclusion': '最终评估已完成，未通过启用条件。'}},
    ])
    router = SimpleNamespace(pick=lambda *a, **k: SimpleNamespace(name='configured-model'), run_task=run)
    evidence = {'accepted': False, 'final_evaluation_completed': True}
    result = generate_summary(evidence, router=router)
    assert result['ai_generated'] and run.call_count == 2
    requests = [json.loads(call.kwargs['prompt']) for call in run.call_args_list]
    assert requests[0]['evidence'] == requests[1]['evidence'] == evidence
    assert 'config_kind missing' in requests[1]['validation_error']


def test_summary_does_not_retry_transport_failure():
    from exceptions import ModelRouteError
    from unittest.mock import Mock
    run = Mock(side_effect=ModelRouteError(code='AI_HTTP_TRANSPORT_ERROR', message='unavailable'))
    router = SimpleNamespace(pick=lambda *a, **k: SimpleNamespace(name='configured-model'), run_task=run)
    with pytest.raises(ModelRouteError):
        generate_summary({}, router=router)
    assert run.call_count == 1


@pytest.mark.parametrize('payload', [
    {'config_kind': 'model_review', 'proposed_value': {'conclusion': '错误类型'}},
    {'config_kind': 'experiment_summary', 'proposed_value': {'conclusion': '  '}},
    {'config_kind': 'experiment_summary', 'proposed_value': {'conclusion': '第一行\n第二行'}},
    {'config_kind': 'experiment_summary', 'proposed_value': {'conclusion': '结束', 'accepted': True}},
])
def test_invalid_model_output_has_no_fallback(payload):
    router = SimpleNamespace(pick=lambda *a, **k: SimpleNamespace(name='configured-model'),
                             run_task=lambda **kwargs: payload)
    with pytest.raises(ValueError):
        generate_summary({}, model_name='configured-model', router=router)
