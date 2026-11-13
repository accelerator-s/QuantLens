from __future__ import annotations
from quantlens.warehouse import job


def run(spark,context,batch_id,through=None,start_at=None,skip=(),mode='research',factor_start=None):
    from quantlens import spark_dwd_prices,spark_dwd_fundamentals,spark_dwd_analyst,spark_dwd_industry,spark_dwd_label,spark_quality,sync_factor_meta,spark_factor_values,spark_factor_eval,spark_weight_fit,spark_kmeans,spark_fpgrowth,spark_pca,spark_engine,spark_portfolio,spark_ads,publish_hbase,spark_audit
    steps=[spark_dwd_prices,spark_dwd_fundamentals,spark_dwd_analyst,spark_dwd_industry,spark_dwd_label,spark_quality,sync_factor_meta,spark_factor_values,spark_factor_eval,spark_weight_fit,spark_kmeans,spark_fpgrowth,spark_pca,spark_engine,spark_factor_eval,spark_portfolio,publish_hbase,spark_audit]
    if mode=='data':
        steps=[spark_dwd_prices,spark_dwd_fundamentals,spark_dwd_analyst,spark_dwd_industry,spark_quality]
    elif mode!='research':
        raise ValueError('Unknown batch mode: '+mode)
    stages={module.__name__.rsplit('.',1)[-1].removeprefix('spark_') for module in steps}
    requested=set(skip)|({start_at} if start_at else set())|({through} if through else set())
    if requested-stages:raise ValueError(f'Unknown pipeline stages: {sorted(requested-stages)}')
    if start_at in {'factor_values','factor_eval','weight_fit','kmeans','fpgrowth','pca','engine'}:sync_factor_meta.run(spark,context)
    import json
    stage_names=[module.__name__.rsplit('.',1)[-1].removeprefix('spark_') for module in steps]
    first=stage_names.index(start_at) if start_at else 0
    total=sum(stage not in skip for stage in stage_names[first:])+1
    completed=0
    started=start_at is None
    from quantlens.stage_cache import StageCache
    cache=StageCache(spark,context)
    for index,module in enumerate(steps):
        stage=module.__name__.rsplit('.',1)[-1].removeprefix('spark_')
        if stage==start_at:started=True
        if not started:continue
        if stage in skip:continue
        spark.sparkContext.setJobGroup(module.__name__,module.__name__)
        print('QUANTLENS_STAGE='+module.__name__,flush=True)
        label={'dwd_prices':'清洗行情','dwd_fundamentals':'清洗财务','dwd_analyst':'清洗预期数据','dwd_industry':'清洗行业数据','dwd_label':'计算评估标签','quality':'检查数据质量','sync_factor_meta':'更新因子定义','factor_values':'计算因子','factor_eval':'评估因子','weight_fit':'拟合因子权重','kmeans':'风格聚类','fpgrowth':'因子共现分析','pca':'降维研究','engine':'训练非线性模型','portfolio':'生成组合','publish_hbase':'发布查询快照','audit':'写入审计'}.get(stage,stage)
        print('QUANTLENS_PROGRESS='+json.dumps({'state':'running','step':label,'phase':label,'step_index':completed+1,'step_total':total,'percent':round(completed/total*100,1)}),flush=True)
        parameters={}
        if module is spark_quality and mode=='data':parameters['include_labels']=False
        elif module is spark_factor_values and factor_start:parameters['start']=factor_start
        elif module is spark_factor_eval and index>steps.index(spark_engine):parameters['name']='model_gbt'
        elif module is spark_factor_eval:parameters['exclude_model']=True
        cache_stage='model_eval' if parameters.get('name')=='model_gbt' else stage
        cache.run(cache_stage,lambda:module.run(spark,context,**parameters),**parameters)
        completed+=1
        spark.catalog.clearCache()
        spark._jvm.System.gc()
        if through and module.__name__.rsplit('.',1)[-1].removeprefix('spark_')==through:break
    print('QUANTLENS_PROGRESS='+json.dumps({'state':'running','step':'发布数据','phase':'发布数据','step_index':total,'step_total':total,'percent':round(completed/total*100,1)}),flush=True)
    if mode=='data':spark_ads.export(spark,context,batch_id,data_only=True)
    else:spark_ads.export(spark,context,batch_id)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--batch',required=True);parser.add_argument('--through');parser.add_argument('--from',dest='start_at');parser.add_argument('--skip',action='append',default=[]);parser.add_argument('--factor-start');parser.add_argument('--mode',choices=['data','research'],default='research');args=parser.parse_args()
    with job('daily') as (spark,context): run(spark,context,args.batch,args.through,args.start_at,args.skip,args.mode,args.factor_start)
