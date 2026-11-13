from quantlens.warehouse import job, lineage, publish

STYLE_FACTORS=['value_defensive_quality_v2','growth_revenue_yoy','industry_momentum_60','quality_gross_margin','volatility_60']


def run(spark,context,train_years=3,seed=42):
    from pyspark.sql import functions as F
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.evaluation import ClusteringEvaluator
    from pyspark.ml.feature import VectorAssembler
    from quantlens.spark_weight_fit import exposures,training_bounds
    source=(exposures(spark,STYLE_FACTORS)
            .repartition(int(spark.conf.get('spark.sql.shuffle.partitions')),'date','code')
            .sortWithinPartitions('date','code'))
    frame=VectorAssembler(inputCols=STYLE_FACTORS,outputCol='features').transform(source)
    years=[r.year for r in frame.select(F.year('date').alias('year')).distinct().orderBy('year').collect()]
    outputs=[]
    for year in years[train_years:]:
        start,end,test_start,test_end=training_bounds(year,train_years)
        train=frame.where((F.col('date')>=start)&(F.col('date')<=end)).persist()
        test=frame.where((F.col('date')>=test_start)&(F.col('date')<=test_end))
        candidates=[]
        from quantlens.gpu import use_gpu, request, ClusterModel
        gpu_candidates={item['k']:ClusterModel(item,STYLE_FACTORS) for item in request('kmeans',train=train,features=STYLE_FACTORS,seed=seed)['candidates']} if use_gpu() else {}
        observations=train.count()
        for k in range(5,9):
            if observations<k: continue
            model=gpu_candidates[k] if gpu_candidates else KMeans(k=k,seed=seed,maxIter=50).fit(train)
            silhouette=ClusteringEvaluator().evaluate(model.transform(train))
            candidates.append((silhouette,k,model))
        if not candidates: raise ValueError('Not enough style observations for k=5..8')
        silhouette,k,model=max(candidates,key=lambda t:(t[0],-t[1]))
        centers=model.clusterCenters()
        # Stable deterministic cluster names within the fitted centroid ordering.
        order=sorted(range(k),key=lambda i:tuple(centers[i]))
        mapping={old:new for new,old in enumerate(order)}
        mapping_expr=F.create_map(*[x for old,new in mapping.items() for x in (F.lit(old),F.lit(new))])
        @F.udf('double')
        def distance(features,prediction):
            import numpy as np
            return float(np.linalg.norm(features.toArray()-centers[prediction]))
        scored=model.transform(test).withColumn('distance',distance('features','prediction')).withColumn('cluster_id',mapping_expr[F.col('prediction')])
        outputs.append(scored.select('code','date','cluster_id','distance',F.lit(k).alias('k'),F.lit(float(model.summary.trainingCost)).alias('inertia'),F.lit(float(silhouette)).alias('silhouette'),F.lit(start).alias('train_start'),F.lit(end).alias('train_end'),F.lit(seed).alias('seed')).withColumn('year',F.year('date')))
        train.unpersist()
    if not outputs: raise ValueError('No out-of-sample clustering window')
    result=outputs[0]
    for frame in outputs[1:]: result=result.unionByName(frame)
    publish(spark,lineage(result,context),'dws.style_cluster',['year'],run=context)

if __name__=='__main__':
    with job('kmeans') as (spark,context): run(spark,context)
