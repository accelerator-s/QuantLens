from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from quantlens.device_runtime import execute_resilient, device_error

GPU={'uuid':'GPU-test','name':'test','free_mib':8000,'total_mib':8000}

@pytest.mark.parametrize('policy,available,expected', [('auto',True,'cuda'),('auto',False,'cpu'),('cuda',False,'cpu'),('cpu',True,'cpu')])
def test_device_selection(tmp_path,policy,available,expected):
    calls=[]
    def attempt(payload,path,device,gpu):
        calls.append(device)
        return {'value':42}
    result=execute_resilient({'device_policy':policy},tmp_path,attempt=attempt,inventory=lambda:[GPU] if available else [])
    assert calls==[expected] and result['device']==expected

@pytest.mark.parametrize('error',['CUDA out of memory','GPU disappeared during execution','CUDA error: device is lost'])
def test_retry_preserves_inputs_and_removes_partial_results(tmp_path,error):
    payload={'device_policy':'auto','seed':42,'train':'immutable'}
    (tmp_path/'train.ready').touch()
    calls=[]
    def attempt(actual,path,device,gpu):
        assert actual==payload
        calls.append(device)
        if device=='cuda':
            (path/'model.ubj').write_text('incomplete')
            return {'error':error,'device_error':device_error('RuntimeError',error)}
        assert not (path/'model.ubj').exists() and (path/'train.ready').exists()
        return {'coefficients':[1,2]}
    result=execute_resilient(payload,tmp_path,attempt=attempt,inventory=lambda:[GPU])
    assert calls==['cuda','cpu'] and result['fallback_reason']==error
    assert result['coefficients']==[1,2]

@pytest.mark.parametrize('error',['bad shape','non-finite input','cancelled'])
def test_application_errors_do_not_retry(tmp_path,error):
    calls=[]
    def attempt(payload,path,device,gpu):
        calls.append(device)
        return {'error':error,'device_error':False}
    result=execute_resilient({},tmp_path,attempt=attempt,inventory=lambda:[GPU])
    assert calls==['cuda'] and result['error']==error

def test_new_request_redetects_reattached_gpu(tmp_path):
    available=[]
    attempt=lambda p,d,device,gpu:{'actual':device}
    assert execute_resilient({},tmp_path,attempt=attempt,inventory=lambda:available)['device']=='cpu'
    available.append(GPU)
    assert execute_resilient({},tmp_path,attempt=attempt,inventory=lambda:available)['device']=='cuda'

@pytest.mark.parametrize('error', ['CUDA error: device-side assert triggered',
                                  'CUDA error: an illegal memory access was encountered',
                                  'cusolver failed to converge: ill-conditioned matrix'])
def test_cuda_programming_errors_do_not_qualify_for_recovery(error):
    assert not device_error('RuntimeError', error)

def test_cpu_and_cuda_standardization_match_pandas():
    from quantlens.gpu import standardize_panel
    from quantlens.device_runtime import gpu_inventory
    raw=pd.DataFrame(np.random.default_rng(42).normal(size=(12,33)),columns=[f's{i}' for i in range(33)])
    raw.iloc[0]=np.nan;raw.iloc[1]=2.;raw.iloc[2,:20]=np.nan;raw.iloc[3,4]=np.inf
    source=raw.replace([np.inf,-np.inf],np.nan).round(6)
    clip=source.clip(lower=source.quantile(.01,axis=1),upper=source.quantile(.99,axis=1),axis=0)
    std=clip.std(axis=1,ddof=0)
    expected=clip.sub(clip.mean(axis=1),axis=0).div(std.replace(0,np.nan),axis=0)
    expected.loc[std.eq(0)]=clip.loc[std.eq(0)]*0
    for policy in ['cpu']+(['cuda'] if gpu_inventory() else []):
        actual,execution=standardize_panel(raw,device_policy=policy)
        assert execution['device']==policy,execution
        np.testing.assert_allclose(actual,expected,rtol=1e-10,atol=1e-12,equal_nan=True)

def test_running_gpu_child_is_terminated_on_hot_removal(tmp_path,monkeypatch):
    from quantlens import device_runtime
    from unittest.mock import Mock
    process=Mock();process.poll.side_effect=[None,0];process.wait.return_value=0
    monkeypatch.setattr(device_runtime.subprocess,'Popen',lambda *a,**k:process)
    monkeypatch.setattr(device_runtime,'gpu_inventory',lambda:[])
    monkeypatch.setattr(device_runtime.time,'monotonic',lambda:100.)
    result=device_runtime.run_attempt({'operation':'linear'},tmp_path,'cuda',GPU)
    process.terminate.assert_called_once()
    assert result['device_error'] and 'disappeared' in result['error']
    assert not list(tmp_path.glob('attempt-*.json'))

def test_failed_cpu_retry_ends_after_two_attempts(tmp_path):
    calls=[]
    def attempt(payload,path,device,gpu):
        calls.append(device)
        return {'error':'CUDA out of memory' if device=='cuda' else 'bad input','device_error':device=='cuda'}
    result=execute_resilient({},tmp_path,attempt=attempt,inventory=lambda:[GPU])
    assert calls==['cuda','cpu'] and result['error']=='bad input'

def test_input_cleanup_keeps_models_and_lazy_prediction_outputs(tmp_path):
    from quantlens.gpu import release_local_inputs
    for name in ('train', 'predict'):
        (tmp_path/name).mkdir()
        (tmp_path/name/'part.parquet').write_bytes(b'temporary input')
        (tmp_path/f'{name}.ready').touch()
    for name in ('model.ubj', 'predictions.parquet', 'response.json'):
        (tmp_path/name).write_bytes(b'acknowledged result')
    release_local_inputs(tmp_path)
    release_local_inputs(tmp_path)
    assert {p.name for p in tmp_path.iterdir()}=={'model.ubj','predictions.parquet','response.json'}

def test_lost_gpu_discards_only_its_unacknowledged_hdfs_prediction(tmp_path,monkeypatch):
    from unittest.mock import Mock
    hdfs=Mock();monkeypatch.setattr('storage.hdfs.HDFS',lambda:hdfs)
    ident='a'*32
    output=f'hdfs://nn-rm:9000/quantlens/_tmp/training/{ident}/output'
    def attempt(payload,path,device,gpu):
        if device=='cuda':return {'error':'GPU disappeared','device_error':True}
        hdfs.rm.assert_called_once_with(output)
        return {'output':output}
    result=execute_resilient({'id':ident,'operation':'predict_tree','output':output},tmp_path,
                            attempt=attempt,inventory=lambda:[GPU])
    assert result['device']=='cpu' and result['output']==output
