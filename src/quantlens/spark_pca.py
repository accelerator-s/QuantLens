from quantlens.warehouse import job, lineage, publish


def run(spark,context,train_end='2022-12-31'):
    from pyspark.sql import functions as F
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.functions import vector_to_array
    from factors.registry import FactorRegistry
    from quantlens.spark_weight_fit import exposures
    names=list(FactorRegistry().get('model_gbt').dependencies)
    frame=VectorAssembler(inputCols=names,outputCol='features').transform(exposures(spark,names)).persist()
    # Complete cases only; fit strictly before the held-out comparison period.
    train=frame.where(F.col('date')<=train_end)
    from quantlens.gpu import request, PCAModel
    model=PCAModel(request('pca',train=train,features=names))
    k=len(model.explainedVariance)
    scores=model.transform(frame).select('code','date',vector_to_array('pca').alias('scores')).withColumn('year',F.year('date'))
    rows=[(name,int(component),float(model.pc.toArray()[index,component]),float(model.explainedVariance[component]),k,train_end) for index,name in enumerate(names) for component in range(k)]
    loadings=spark.createDataFrame(rows,'factor string, component int, loading double, explained_variance double, k int, train_end string').withColumn('as_of',F.current_date())
    publish(spark,lineage(scores,context),'dws.pca_scores',['year'],run=context)
    publish(spark,lineage(loadings,context),'dws.pca_loadings',['as_of'],run=context)
    from quantlens.gpu import fit_tree
    from quantlens.spark_weight_fit import fit
    from quantlens.model_comparison import summarize
    labels=spark.table('dwd.label_forward').where("horizon=5 AND label_version='v1' AND valid").select('code','date','ret','valid_until')
    data=frame.join(labels,['code','date'])
    data=model.transform(data)
    array=vector_to_array('pca');components=[f'pc_{i}' for i in range(k)]
    for i,name in enumerate(components):data=data.withColumn(name,array[i])
    train=data.where((F.col('date')<=train_end)&(F.col('valid_until')<=train_end)).persist()
    test=data.where(F.col('date')>train_end).persist()
    test_start=str(test.agg(F.min('date').alias('date')).first().date)
    comparisons=[]
    for representation,columns in [('raw',names),('pca',components)]:
        for method in ['model_linear','model_gbt']:
            if method=='model_linear':
                weights=fit(spark,train,columns,method)
                scored=test.withColumn('score',sum(F.col(name)*float(weight) for name,weight in zip(columns,weights)))
            else:
                assembler=VectorAssembler(inputCols=columns,outputCol='_research_features')
                fitted=fit_tree(train,columns,seed=42)
                scored=fitted.transform(assembler.transform(test)).withColumnRenamed('prediction','score')
            metrics=summarize(scored)
            comparisons.append(('pca',test_start,representation+'_'+method,*[metrics[key] for key in ['rank_ic','top_quintile_return','turnover','max_drawdown','objective_value']],metrics['evaluation']))
    comparison=spark.createDataFrame(comparisons,'experiment string, test_start string, method string, rank_ic double, top_quintile_return double, turnover double, max_drawdown double, objective_value double, evaluation string').withColumn('as_of',F.current_date())
    import json
    comparison=comparison.withColumn('factor_version',F.lit('research')).withColumn('feature_versions',F.lit(json.dumps({name:FactorRegistry().get(name).version for name in names},sort_keys=True)))
    from quantlens.model_comparison import publish_comparison
    publish_comparison(spark,comparison,context,'pca')
    train.unpersist();test.unpersist();frame.unpersist()

if __name__=='__main__':
    with job('pca') as (spark,context): run(spark,context)
