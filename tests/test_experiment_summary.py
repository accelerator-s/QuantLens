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
