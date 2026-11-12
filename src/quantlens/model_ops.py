import json
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo

from exceptions import ContractViolation
from paths import workspace
from quantlens import data,store
from quantlens.spark_engine import start_session
from quantlens.spark_features import FEATURES
from quantlens.provenance import feature_builder

def infer(run_id,as_of,reporter):
    from pyspark.sql import functions as F
    from pyspark.ml import PipelineModel
    root=store.run_path(run_id)
    config=json.loads((root/'config.json').read_text())
    if not (root/'metrics.json').exists():
        raise ContractViolation(code='QL_MODEL_INCOMPLETE',message='模型评估尚未完成',suggested_action='先完成研究任务')
    if as_of<=date.fromisoformat(config['config']['train_end']):
        raise ContractViolation(code='QL_INFERENCE_IN_TRAIN',message='预测日期落在训练期内',suggested_action='选择训练截止日之后的日期')
    if as_of.isoformat()>data.coverage()['end'][:10]:
        raise ContractViolation(code='QL_DATE_UNCOVERED',message='日期超过行情水位',suggested_action='先更新数据')
    actual=data.query('SELECT max(CAST(date AS DATE)) date FROM read_parquet(?) WHERE CAST(date AS DATE)<=?',[data.table('prices_daily'),as_of])[0]['date'][:10]
    if any(r['body']['run_id']==run_id and r['body']['as_of']==actual for r in store.records('forecast',5000)):
        raise ContractViolation(code='QL_FORECAST_EXISTS',message='该模型当日预测已保存',suggested_action='查看已有预测，避免重复计入跟踪结果')
    dataset_id,source=data.freeze(reporter)
    inference_id=store.uid('FORECAST')
    output=workspace().runs/'forecasts'/inference_id
    spark=start_session(4,inference_id)
    try:
        model=PipelineModel.load(str(root/'model'))
        build=feature_builder(root)
        reporter('计算全市场预测特征')
        df=build(spark,source,str(as_of-timedelta(days=10)),str(as_of),0).where(F.col('eligible'))
        latest=df.agg(F.max('date')).first()[0]
        if latest is None:
            raise ContractViolation(code='QL_NO_ELIGIBLE_STOCKS',message='该日期没有满足窗口要求的证券',suggested_action='选择覆盖充分的交易日期')
        reporter('使用已保存模型预测')
        predictions=model.transform(df.where(F.col('date')==F.lit(latest)))
        predictions.select('instrument_id','date','close','prediction',*FEATURES).write.mode('error').parquet(str(output))
        rows=predictions.orderBy(F.desc('prediction'),'instrument_id').select('instrument_id','date','close','prediction').limit(100).toPandas().to_dict('records')
        values={'run_id':run_id,'dataset_id':dataset_id,'as_of':str(latest),'items':rows,
                'artifact':str(output.relative_to(workspace().root)),
                'mode':forecast_mode(latest),
                'feature_provenance':'frozen' if (root/'source/manifest.json').exists() else 'legacy',
                'model_state':'research','created_at':store.now()}
        return store.add('forecast',values,inference_id)
    finally:spark.stop()

def observe():
    known={r['body']['forecast_id'] for r in store.records('forecast_outcome',5000)}
    outcomes=[]
    for r in store.records('forecast',1000):
        if r['id'] in known:continue
        forecast=r['body']
        dates=data.query('''SELECT DISTINCT date FROM read_parquet(?) WHERE date>? ORDER BY date LIMIT 6''',
                         [data.table('prices_daily'),forecast['as_of']])
        if len(dates)<6:continue
        start,end=dates[0]['date'],dates[-1]['date']
        realized=data.query('''SELECT a.instrument_id,b.adj_close_wd/a.adj_close_wd-1 AS realized
            FROM read_parquet(?) a JOIN read_parquet(?) b USING(instrument_id)
            WHERE a.date=? AND b.date=? AND a.adj_close_wd>0''',
            [data.table('returns_daily'),data.table('returns_daily'),start,end])
        lookup={x['instrument_id']:x['realized'] for x in realized}
        all_predictions=data.query('SELECT instrument_id,prediction FROM read_parquet(?)',[str(workspace().root/forecast['artifact']/'*.parquet')])
        rows=[{**x,'realized':lookup.get(x['instrument_id'])} for x in all_predictions]
        valid=[x for x in rows if x['realized'] is not None]
        if not valid:continue
        import pandas as pd
        df=pd.DataFrame(valid)
        rank_ic=float(df.prediction.rank().corr(df.realized.rank()))
        outcome={'forecast_id':r['id'],'run_id':forecast['run_id'],'as_of':forecast['as_of'],
            'label_end':end,'mode':forecast_mode(date.fromisoformat(forecast['as_of']),datetime.fromisoformat(forecast['created_at'])),'samples':len(valid),'rank_ic':rank_ic,
            'top20_return':float(df.nlargest(20,'prediction').realized.mean()),
            'mean_return':float(df.realized.mean()),'items':rows,'unmatched':len(rows)-len(valid)}
        outcomes.append(store.add('forecast_outcome',outcome))
    return {'items':outcomes}

def drift(run_id):
    values=[r['body'] for r in store.records('forecast_outcome',1000) if r['body']['run_id']==run_id]
    values=sorted(values,key=lambda x:x['as_of'])
    unique={}
    for x in values:
        if x['mode']=='shadow' and x['rank_ic'] is not None:unique.setdefault(x['as_of'],x)
    real=list(unique.values())
    recent=real[-20:]
    ic=sum(x['rank_ic'] for x in recent)/len(recent) if recent else None
    alert=len(recent)>=20 and all(x['rank_ic']<0 for x in recent[-5:]) and ic<0
    return {'run_id':run_id,'observations':len(real),'recent_rank_ic':ic,'alert':alert,
            'minimum_observations':20,'items':values,
            'state':'watching' if alert else 'observing' if len(real)<20 else 'reviewable'}

def allocate(forecast_id,capital,max_weight,reserve,top_n,account_id=''):
    forecast=store.get(forecast_id)
    if forecast['kind']!='forecast':
        raise ContractViolation(code='QL_FORECAST_REQUIRED',message='请选择预测记录',suggested_action='先运行模型预测')
    holdings=[]
    if account_id:
        from portfolio import AccountRegistry,PortfolioStore
        account=AccountRegistry().get(account_id)
        holdings=PortfolioStore().read_holdings(account_id,market='CN-A',instrument_class='stock').to_dict('records')
    weight=min(max_weight,(1-reserve)/top_n)
    selected=forecast['body']['items'][:top_n]
    targets=[]
    for row in selected:
        qty=int(capital*weight/row['close']/100)*100
        targets.append({**row,'quantity':qty,'weight':qty*row['close']/capital,
                        'reason':'预测排序与单票权重约束'})
    return store.add('allocation_scenario',{'forecast_id':forecast_id,'account_id':account_id,
        'capital':capital,'targets':targets,'cash':capital-sum(x['quantity']*x['close'] for x in targets),
        'current_holdings':holdings,'state':'research','note':'研究情景；不能确认成真实交易计划。正式计划须经过策略评估与人工晋升。'})


def forecast_mode(signal_date,now=None):
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    if signal_date==local.date() and local.hour>=15:return 'shadow'
    if signal_date==local.date()-timedelta(days=1) and (local.hour,local.minute)<(9,30):return 'shadow'
    return 'historical'


def explain(forecast_id,code):
    record=store.get(forecast_id)
    if record['kind']!='forecast':
        raise ContractViolation(code='QL_FORECAST_REQUIRED',message='请选择预测记录',suggested_action='从预测列表选择')
    body=record['body']
    root=store.run_path(body['run_id'])
    metrics=json.loads((root/'metrics.json').read_text())
    artifact=str(workspace().root/body['artifact']/'*.parquet')
    selected=[r for r in metrics['importance'] if r['feature'] in FEATURES]
    ranks=','.join(f'percent_rank() OVER(ORDER BY "{r["feature"]}" NULLS LAST) AS "{r["feature"]}_rank"' for r in selected)
    rows=data.query(f'SELECT * FROM (SELECT *,{ranks} FROM read_parquet(?)) WHERE instrument_id=?',[artifact,code])
    if not rows:
        raise ContractViolation(code='QL_FORECAST_STOCK_NOT_FOUND',message='本次预测中没有该股票',suggested_action='选择有预测记录的股票')
    row=rows[0]
    radius=json.loads((root/'calibration.json').read_text())['absolute_error_q90']
    features=[{**r,'value':row[r['feature']],'percentile':row[r['feature']+'_rank'] if row[r['feature']] is not None else None} for r in selected]
    return {'forecast_id':forecast_id,'run_id':body['run_id'],'instrument_id':code,'as_of':body['as_of'],
            'prediction':row['prediction'],'lower':row['prediction']-radius,'upper':row['prediction']+radius,'features':sorted(features,key=lambda r:r['importance'],reverse=True),
            'note':'分位表示因子在本次预测股票中的位置。重要性来自整体模型，不代表个股收益贡献。误差区间由验证期残差估计，未来覆盖率可能下降。'}
