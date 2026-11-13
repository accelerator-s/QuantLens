import json
from datetime import date

from pydantic import BaseModel,Field,ConfigDict
from quantlens import data,store

class Criteria(BaseModel):
    model_config=ConfigDict(extra='forbid')
    as_of:date
    max_price:float=Field(default=40,gt=0)
    min_amount:float=Field(default=10000000,ge=0)
    min_momentum:float=Field(default=-1,ge=-1,le=10)
    max_position:float=Field(default=1,ge=0,le=1)
    min_roe:float|None=None
    max_debt:float|None=Field(default=None,ge=0,le=1)
    industry:str=''
    limit:int=Field(default=100,ge=1,le=1000)
    account_id:str=''

def execute(c):
    from quantlens import ads
    from exceptions import ContractViolation
    from portfolio.accounts import AccountRegistry, DEFAULT_ACCOUNT_ID
    account = AccountRegistry().get(c.account_id or DEFAULT_ACCOUNT_ID)
    c = c.model_copy(update={'account_id': account.account_id})
    calendar=ads.query('market','date<=CAST(? AS DATE)',[str(c.as_of)],limit=1,order='date DESC')
    if not calendar:
        raise ContractViolation(code='SCREEN_DATE_NOT_PUBLISHED',message='该日期不在已发布的选股结果范围内',suggested_action='选股和个股页面使用最近 400 天；完整历史研究使用 DWD/DWS 研究窗口')
    selected=calendar[0]['date']
    clauses=['date=CAST(? AS DATE)','in_universe','close<=?','amount>=?','momentum_20>=?','position_60<=?']
    params=[selected,c.max_price,c.min_amount,c.min_momentum,c.max_position]
    if account is not None and not account.star_market_enabled:
        clauses.extend(["code NOT LIKE '688%'", "code NOT LIKE '689%'"])
    for column,value,operator in [('roe_ttm',c.min_roe,'>='),('debt_ratio',c.max_debt,'<=')]:
        if value is not None: clauses.append(column+operator+'?');params.append(value)
    if c.industry: clauses.append('(industry_code=? OR industry_name=?)');params.extend([c.industry,c.industry])
    result=ads.query('screen',' AND '.join(clauses),params,limit=c.limit,order='date DESC, amount DESC, code')
    for row in result: row['instrument_id']=row['code']
    return {'items':result,'criteria':c.model_dump(mode='json'),'notes':['DWD 清洗及 PIT 后的 ADS 选股结果'],**ads.provenance('screen')}


def translate(question,as_of,model_name=''):
    from ai.router import AIRouter
    from webui.backend.ai_chat import pick_chat_model,chat_once
    ai=AIRouter();model=pick_chat_model(ai,model_name)
    raw=chat_once(ai,model,[{'role':'system','content':'将选股请求转换为JSON对象，严格遵守给定schema。'
        '不得生成SQL或代码。未明确条件使用schema默认值。只返回JSON。schema:'+json.dumps(Criteria.model_json_schema(),ensure_ascii=False)},
        {'role':'user','content':store.encode({'request':question,'as_of':str(as_of)})}])
    criteria=Criteria.model_validate_json(raw.strip().removeprefix('```json').removesuffix('```').strip())
    return {'criteria':criteria.model_dump(mode='json'),'model':model.name,'requires_review':True}
