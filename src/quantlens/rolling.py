import json
from datetime import date

from pydantic import BaseModel,Field

from exceptions import ContractViolation
from paths import workspace
from quantlens import store,data
from quantlens.spark_engine import ResearchConfig,run,resume


class RollingConfig(BaseModel):
    name:str='A股滚动年度验证'
    first_test_year:int=Field(default=2023,ge=2005,le=2100)
    folds:int=Field(default=3,ge=2,le=6)
    train_years:int=Field(default=3,ge=1,le=10)
    stock_limit:int=Field(default=0,ge=0,le=7000)
    cores:int=Field(default=4,ge=1,le=12)
    trees:int=Field(default=30,ge=5,le=150)
    depth:int=Field(default=3,ge=2,le=6)
    top_n:int=Field(default=30,ge=5,le=100)
    cost_bps:float=Field(default=15,ge=0,le=100)


def windows(c):
    return [ResearchConfig(name=f'{c.name} / {year}',start=date(year-c.train_years-1,1,1),
        train_end=date(year-2,12,31),valid_end=date(year-1,12,31),end=date(year,12,31),
        **c.model_dump(include={'stock_limit','cores','trees','depth','top_n','cost_bps'}))
        for year in range(c.first_test_year,c.first_test_year+c.folds)]


def create(c):
    configs=windows(c)
    if str(configs[-1].end)>data.coverage()['end'][:10]:
        raise ContractViolation(code='QL_ROLLING_DATE_UNCOVERED',message='最后一个年度尚未完整覆盖',suggested_action='减少折数或提前首个评估年份')
    return store.add('rolling_plan',{'config':c.model_dump(),'windows':[{'run_id':store.uid('QL'),'config':x.model_dump(mode='json')} for x in configs]})


def execute(batch_id,reporter):
    record=store.get(batch_id)
    if record['kind']!='rolling_plan':
        raise ContractViolation(code='QL_ROLLING_PLAN_REQUIRED',message='请选择滚动研究',suggested_action='从滚动列表选择')
    for i,item in enumerate(record['body']['windows']):
        root=store.run_path(item['run_id'])
        if (root/'metrics.json').exists():continue
        reporter(f'第 {i+1} / {len(record["body"]["windows"])} 折：{item["config"]["end"][:4]}')
        if (root/'config.json').exists():resume(item['run_id'],reporter)
        else:run(ResearchConfig(**item['config']),reporter,item['run_id'])
    result=detail(batch_id)
    store.add('rolling_result',result)
    return result


def detail(batch_id):
    record=store.get(batch_id)
    items=[]
    for item in record['body']['windows']:
        p=store.run_path(item['run_id'])/'metrics.json'
        value=json.loads(p.read_text()) if p.exists() else None
        items.append({**item,'metrics':value['metrics'] if value else None,
                      'simulation':value['simulation'] if value else None,
                      'resumable':not (p.parent/'evaluation_opened.json').exists()})
    completed=[x for x in items if x['metrics']]
    return {'batch_id':batch_id,'config':record['body']['config'],'items':items,
            'completed':len(completed),'state':'completed' if len(completed)==len(items) else 'incomplete',
            'mean_rank_ic':sum(x['metrics']['rank_ic'] for x in completed)/len(completed) if completed else None,
            'note':'每年重新筛选和训练，评估年份不重叠。仅汇总已完成年份，相关系数均值不代表统计显著性。'}
