from unittest.mock import Mock

import pytest

from exceptions import LifecycleError
from lifecycle.models import ResearchProgram
from lifecycle.reporting import ResearchReportService, _NARRATIVE_FIELDS


def payload():
    return {'config_kind':'research_report_narrative','model_name':'Codex',
            'proposed_value':{name:'固定规则下未通过，历史结果不保证未来收益。' for name in _NARRATIVE_FIELDS}}


@pytest.mark.parametrize('purpose',['train','walk_forward'])
def test_direct_report_keeps_artifact_citations_without_routing_another_model(monkeypatch,tmp_path,purpose):
    from lifecycle.models import EvaluationRun, ObjectType
    import yaml
    monkeypatch.setenv('QUANTLENS_HOME',str(tmp_path))
    program=ResearchProgram(program_id='RP-test',loop='factor_research',objective='test',
        hypothesis_scope=['test'],data_snapshot_id='snapshot',protocol_hash='protocol',
        train_window={'start':'2020-01-01','end':'2020-12-01'},
        validation_window={'start':'2021-01-01','end':'2021-12-01'},
        locked_test_window={'start':'2022-01-01','end':'2022-12-01'},
        targets={'min_annualized_excess_return':.03},random_seed=42,
        configuration={'model_name':'unregistered-direct-author'},
        budget={'max_iterations':1,'max_model_calls':1,'max_runtime_seconds':60})
    run=EvaluationRun(evaluation_run_id='ER-test',subject_type='factor',subject_id='FV-test',
        research_program_id='RP-test',data_snapshot_id='snapshot',purpose=purpose,
        window={'start':'2021-01-01','end':'2021-12-01'},protocol_hash='protocol',
        artifact_ref='result',artifact_hash='hash')
    path=tmp_path/'result';path.mkdir()
    metrics={'returns':{'annualized_excess_return':-.05},'risk':{'max_drawdown':-.2}}
    (path/'metrics.yaml').write_text(yaml.safe_dump(metrics))
    store=Mock();store.get.return_value=program;store.list_objects.return_value=[]
    service=ResearchReportService(store)
    monkeypatch.setattr(service,'_subjects',lambda *args:['FV-test'])
    monkeypatch.setattr(service,'_runs',lambda *args:[(run,metrics)])
    def unexpected(*args):raise AssertionError('must not call configured model')
    monkeypatch.setattr(service,'_ai_narrative',unexpected)
    report=service.generate('RP-test',narrative_payload=payload())
    assert len(report.metric_citations)==2
    assert report.metric_citations[0].rendered_value=='-5.00%'
    artifact=tmp_path/report.report_artifact_ref
    assert artifact.is_file() and 'Codex' in artifact.read_text()
    content=artifact.read_text()
    assert '数据快照：' not in content
    assert '固定协议哈希：' not in content
    assert '历史样本外结果' not in content
    assert '使用折级符号检验和 Bonferroni 校正' not in content
    assert f'| {purpose} |' in content
    with pytest.raises(LifecycleError) as error:
        invalid=payload();invalid['proposed_value']['plain_conclusion']='超额收益99%'
        service.generate('RP-test',narrative_payload=invalid)
    assert error.value.code=='RESEARCH_REPORT_AI_NUMERIC_CLAIM'


@pytest.mark.parametrize('bad',[{}, {'config_kind':'wrong'},
    {'config_kind':'research_report_narrative','proposed_value':{}}])
def test_invalid_direct_report_fails_without_substitution(bad):
    with pytest.raises(LifecycleError):ResearchReportService._validated_narrative(bad)
