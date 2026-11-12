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

def execute(c):
    result=data.query('''WITH history AS (
      SELECT p.*,r.adj_close_wd,r.ret_wd,r.trading_status,r.mktcap_total,
        lag(r.adj_close_wd,20) OVER w AS lag20,
        min(r.adj_close_wd) OVER w60 AS low60,max(r.adj_close_wd) OVER w60 AS high60,
        count(*) OVER w60 AS observations
      FROM read_parquet(?) p JOIN read_parquet(?) r USING(instrument_id,date)
      WHERE CAST(p.date AS DATE) BETWEEN CAST(? AS DATE)-INTERVAL 150 DAY AND CAST(? AS DATE)
      WINDOW w AS (PARTITION BY p.instrument_id ORDER BY p.date),
             w60 AS (PARTITION BY p.instrument_id ORDER BY p.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)),
      latest AS (SELECT *,adj_close_wd/NULLIF(lag20,0)-1 AS momentum_20,
        (adj_close_wd-low60)/NULLIF(high60-low60,0) AS position_60 FROM history
        WHERE date=(SELECT max(date) FROM history)),
      fundamentals AS (SELECT instrument_id,roe_ttm FROM read_parquet(?)
        WHERE CAST(effective_date AS DATE)<=CAST(? AS DATE) AND ann_date_imputed IS NULL
        QUALIFY row_number() OVER(PARTITION BY instrument_id ORDER BY effective_date DESC, report_period DESC)=1),
      balance AS (SELECT instrument_id,total_liabilities/NULLIF(total_assets,0) AS debt_ratio FROM read_parquet(?)
        WHERE CAST(effective_date AS DATE)<=CAST(? AS DATE) AND ann_date_imputed IS NULL
        QUALIFY row_number() OVER(PARTITION BY instrument_id ORDER BY effective_date DESC,report_period DESC)=1),
      industry AS (SELECT instrument_id,industry_name FROM read_parquet(?)
        WHERE CAST(effective_date AS DATE)<=CAST(? AS DATE) AND classification_id='P0221'
        QUALIFY row_number() OVER(PARTITION BY instrument_id ORDER BY effective_date DESC)=1)
      SELECT l.*,f.roe_ttm,b.debt_ratio,i.industry_name FROM latest l
      LEFT JOIN fundamentals f USING(instrument_id) LEFT JOIN balance b USING(instrument_id)
      LEFT JOIN industry i USING(instrument_id)
      WHERE close>0 AND close<=? AND amount>=? AND volume>0 AND observations>=60
        AND CAST(trading_status AS INTEGER) NOT IN (2,3,5,6,8,9,11,12,14,15,16)
        AND momentum_20>=? AND position_60<=?
        AND (? IS NULL OR roe_ttm>=?) AND (? IS NULL OR debt_ratio<=?)
        AND (?='' OR industry_name=?) ORDER BY amount DESC,instrument_id LIMIT ?''',
        [data.table('prices_daily'),data.table('returns_daily'),str(c.as_of),str(c.as_of),
         data.table('fundamental_ttm'),str(c.as_of),data.table('fs_balance'),str(c.as_of),
         data.table('industry_classification'),str(c.as_of),c.max_price,c.min_amount,
         c.min_momentum,c.max_position,c.min_roe,c.min_roe,c.max_debt,c.max_debt,c.industry,c.industry,c.limit])
    return {'items':result,'criteria':c.model_dump(mode='json'),
            'notes':['技术窗口按有记录的交易日计算，停牌会延长实际跨度。',
                     '财务指标排除补估公告日，但更正前历史版本未完全覆盖。']}

def translate(question,as_of,model_name=''):
    from ai.router import AIRouter
    from webui.backend.ai_chat import pick_chat_model,chat_once
    ai=AIRouter();model=pick_chat_model(ai,model_name)
    raw=chat_once(ai,model,[{'role':'system','content':'将选股请求转换为JSON对象，严格遵守给定schema。'
        '不得生成SQL或代码。未明确条件使用schema默认值。只返回JSON。schema:'+json.dumps(Criteria.model_json_schema(),ensure_ascii=False)},
        {'role':'user','content':store.encode({'request':question,'as_of':str(as_of)})}])
    criteria=Criteria.model_validate_json(raw.strip().removeprefix('```json').removesuffix('```').strip())
    return {'criteria':criteria.model_dump(mode='json'),'model':model.name,'requires_review':True}
