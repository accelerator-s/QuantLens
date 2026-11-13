"""GBT is a registered factor; features and labels come from the warehouse."""
from quantlens.warehouse import job, lineage, publish


def identity(names,seed=42,train_years=3):
    import hashlib,json
    from factors.registry import FactorRegistry
    versions={name:FactorRegistry().get(name).version for name in names}
    return hashlib.sha256(open(__file__,'rb').read()+open(__file__.replace('spark_engine.py','gpu_worker.py'),'rb').read()+json.dumps({'names':names,'versions':versions,'seed':seed,'train_years':train_years},sort_keys=True).encode()).hexdigest()[:16]

def run(spark,context,train_years=3,seed=42,version=None):
    import hashlib,json
    from pyspark.sql import functions as F
    from pyspark.ml.feature import VectorAssembler
    from quantlens.gpu import fit_tree
    from factors.registry import FactorRegistry
    from quantlens.spark_weight_fit import exposures,training_bounds
    from quantlens.standardize import standardize
    meta=FactorRegistry().get('model_gbt')
    version=version or meta.version
    names=list(meta.dependencies)
    values=exposures(spark,names)
    labels=spark.table('dwd.label_forward').where("horizon=5 AND label_version='v1'").select('code','date','ret','valid_until')
    frame=VectorAssembler(inputCols=names,outputCol='features').transform(values.join(labels,['code','date'],'left')).persist()
    years=[r.year for r in frame.select(F.year('date').alias('year')).distinct().orderBy('year').collect()]
    outputs=[];comparisons=[]
    code_hash=identity(names,seed,train_years)
    if spark.catalog.tableExists('dws.factor_values'):
        old=spark.table('dws.factor_values').where((F.col('factor')=='model_gbt')&(F.col('version')==version)).select('code_hash').distinct().collect()
        if any(r.code_hash!=code_hash for r in old): raise ValueError('model_gbt implementation changed: increment version')
    for year in years[train_years:]:
        start,end,test_start,test_end=training_bounds(year,train_years)
        train=frame.where((F.col('date')>=start)&(F.col('date')<=end)&(F.col('valid_until')<test_start)&F.col('ret').isNotNull()).persist()
        test=frame.where((F.col('date')>=test_start)&(F.col('date')<=test_end)).persist()
        model=fit_tree(train,names,seed=seed)
        from quantlens.spark_weight_fit import fit
        from quantlens.model_comparison import summarize
        mature_test=test.where((F.col('valid_until')<=test_end)&F.col('ret').isNotNull())
        predictions=model.transform(mature_test).withColumnRenamed('prediction','score')
        for method in ['model_gbt','icir','model_linear']:
            if method=='model_gbt':scored=predictions
            else:
                weights=fit(spark,train,names,method)
                scored=mature_test.withColumn('score',sum(F.col(name)*float(weight) for name,weight in zip(names,weights)))
            metrics=summarize(scored)
            comparisons.append(('gbt',test_start,method,*[metrics[key] for key in ['rank_ic','top_quintile_return','turnover','max_drawdown','objective_value']],metrics['evaluation']))
        raw=model.transform(test).select('code','date',F.round('prediction',6).alias('value'))
        normalized=raw.groupBy('date').applyInPandas(lambda p:standardize(p)[['code','date','value','std_value','coverage']], 'code string, date date, value double, std_value double, coverage double')
        outputs.append(normalized.withColumn('year',F.year('date')))
        train.unpersist();test.unpersist()
    if not outputs: raise ValueError('No complete rolling GBT window')
    result=outputs[0]
    for part in outputs[1:]: result=result.unionByName(part)
    result=lineage(result,context,version).withColumn('factor',F.lit('model_gbt')).withColumn('code_hash',F.lit(code_hash))
    publish(spark,result,'dws.factor_values',['factor','version','year'],run=context)
    comparison=spark.createDataFrame(comparisons,'experiment string, test_start string, method string, rank_ic double, top_quintile_return double, turnover double, max_drawdown double, objective_value double, evaluation string').withColumn('as_of',F.current_date())
    import json
    comparison=comparison.withColumn('factor_version',F.lit(version)).withColumn('feature_versions',F.lit(json.dumps({name:FactorRegistry().get(name).version for name in names},sort_keys=True)))
    from quantlens.model_comparison import publish_comparison
    publish_comparison(spark,comparison,context,'gbt')
    frame.unpersist()

if __name__=='__main__':
    with job('model_gbt') as (spark,context):
        from quantlens.sync_factor_meta import run as sync_meta
        sync_meta(spark,context)
        run(spark,context)
