"""Measure the published serving APIs; never substitute generated fixture data."""
import json,time
from pathlib import Path
import httpx,numpy as np
base='http://127.0.0.1:8611'
with httpx.Client(base_url=base,trust_env=False,timeout=30) as client:
    overview=client.get('/api/overview');overview.raise_for_status()
    end=overview.json()['coverage']['end']
    assert end,'No published warehouse coverage'
    report={'data_as_of':end,'run_id':overview.json()['run_id'],'data_snapshot':overview.json()['data_snapshot'],'latency':{},'contracts':{}}
    cases=[('overview','GET','/api/overview',None,.5),('screen','POST','/api/screen',{'as_of':end},1.),('stock','GET','/api/stocks/000001',None,.5),('factor','GET','/api/factors/reversal_5',None,.8)]
    for name,method,path,payload,limit in cases:
        samples=[]
        for _ in range(20):
            start=time.perf_counter();response=client.request(method,path,json=payload) if payload else client.request(method,path)
            response.raise_for_status();samples.append(time.perf_counter()-start)
            assert {'data_as_of','run_id','layer','source_uri'}<=response.json().keys(),(name,response.json().keys())
        p95=float(np.percentile(samples,95));report['latency'][name]={'p95_seconds':p95,'samples':len(samples),'limit_seconds':limit,'passed':p95<limit}
    nonempty=['/api/factors/reversal_5/values','/api/factors/reversal_5/evaluation','/api/strategies/candidate_balanced_multifactor/weights','/api/portfolio/candidate_balanced_multifactor/candidates','/api/portfolio/candidate_balanced_multifactor/holdings','/api/portfolio/research_reversal_5/attribution','/api/backtest/runs']
    for path in nonempty+['/api/settings/data','/api/settings/cluster','/api/settings/audit']:
        response=client.get(path);response.raise_for_status();report['contracts'][path]=response.status_code
        if path in nonempty:assert response.json()['items'],path
    point=client.get('/api/factors/reversal_5/values',params={'as_of':end,'code':'000001','version':'v1'})
    point.raise_for_status();assert point.json()['layer']=='hbase',point.json()
    holdings=client.get('/api/portfolio/candidate_balanced_multifactor/holdings').json()
    assert holdings['layer']=='hbase',holdings
    run_id=client.get('/api/backtest/runs').json()['items'][0]['run_id']
    for suffix in ['', '/nav', '/trades', '/positions']:
        path='/api/backtest/runs/'+run_id+suffix
        response=client.get(path);response.raise_for_status()
        if suffix:assert response.json()['items'],path
        report['contracts'][path]=response.status_code
    Path('docs/course/evidence/api-verification.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,ensure_ascii=False))
    assert all(item['passed'] for item in report['latency'].values()),report['latency']
