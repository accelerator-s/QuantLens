from quantlens.warehouse import job, lineage, publish

def run(spark,context,train_years=3,seed=42,end=None,start=None):
    from pyspark.sql import functions as F
    from pyspark.ml.evaluation import ClusteringEvaluator
    from pyspark.ml.feature import VectorAssembler
    from pyspark import StorageLevel
    from quantlens.spark_weight_fit import training_bounds
    from quantlens.style_features import market_source, carry_prediction_features, STYLE_FEATURES, STYLE_METHOD_VERSION
    source=market_source(spark,start=start,end=end)
    names=STYLE_FEATURES
    method_version=STYLE_METHOD_VERSION
    if end:
        # Bound the input before enumerating years or fitting any centroids.
        # This permits research repairs without reading a reserved test period.
        from datetime import date
        date.fromisoformat(end)
        source=source.where(F.col('date')<=end)
    if start:
        source=source.where(F.col('date')>=start)
    source=(source
            .repartition(int(spark.conf.get('spark.sql.shuffle.partitions')),'date','code')
            .sortWithinPartitions('date','code'))
    # Reuse the same bounded, ordered exposure matrix for every rolling year.
    # Disk caching avoids recomputing market ranks
    # without taking memory away from each window's training data.
    frame=VectorAssembler(inputCols=names,outputCol='features').transform(source).persist(StorageLevel.DISK_ONLY)
    years=[r.year for r in frame.select(F.year('date').alias('year')).distinct().orderBy('year').collect()]
    if frame.where('_observed_features=0').limit(1).count():
        frame.unpersist()
        raise ValueError('Style classification contains stocks with no observed market features')
    prediction = (VectorAssembler(inputCols=names,outputCol='features').transform(
        carry_prediction_features(frame)).persist(StorageLevel.DISK_ONLY))
    from quantlens.gpu import request, ClusterModel
    outputs=[]
    fitted_models=[]
    for year in years[train_years:]:
        start,end,test_start,test_end=training_bounds(year,train_years)
        print(f'QUANTLENS_CLUSTER_WINDOW={start}..{end} -> {test_start}..{test_end}',flush=True)
        train=frame.where((F.col('date')>=start)&(F.col('date')<=end))
        # Four numerical dimensions fit in eight partitions. Avoid scheduling
        # dozens of tiny tasks on every Lloyd iteration when CUDA is unavailable.
        train=train.coalesce(8)
        train=train.persist()
        test=prediction.where((F.col('date')>=test_start)&(F.col('date')<=test_end))
        candidates=[]
        execution=request('kmeans',train=train,features=names,seed=seed)
        fitted_candidates={item['k']:ClusterModel(item,names) for item in execution['candidates']}
        observations=train.count()
        for k in range(5,9):
            if observations<k: continue
            model=fitted_candidates[k]
            silhouette=ClusteringEvaluator().evaluate(model.transform(train))
            candidates.append((silhouette,k,model))
        if not candidates: raise ValueError('Not enough style observations for k=5..8')
        silhouette,k,model=max(candidates,key=lambda t:(t[0],-t[1]))
        print(f'QUANTLENS_CLUSTER_FITTED={year},k={k},silhouette={silhouette}',flush=True)
        centers=model.clusterCenters()
        fitted_models.append({'prediction_year':year,'k':k,'silhouette':float(silhouette),
                              'centers':[list(map(float,center)) for center in centers],
                              'device':execution['device'],'device_attempts':execution['device_attempts'],
                              'fallback_reason':execution['fallback_reason']})
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
    for part in outputs[1:]: result=result.unionByName(part)
    from quantlens.security_master import normalize_style_identity, CODE_CHANGES
    result=normalize_style_identity(result)
    try:
        from quantlens.warehouse import write_json, root_uri
        write_json(spark,root_uri()+'/meta/runs/kmeans/'+context['run_id']+'/method',
                   {'method_version':method_version,'features':names,'train_years':train_years,
                    'seed':seed,'source':'market','prediction_carry':True,
                    'synthetic_rows_used_for_training':False,'models':fitted_models,
                    'backend':'torch_float64_v2',
                    'security_master_events':[{key:str(value) for key,value in event.items()} for event in CODE_CHANGES],**context})
        publish(spark,lineage(result,context,version=method_version),'dws.style_cluster',['year'],run=context)
    finally:
        frame.unpersist()
        prediction.unpersist()

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--end');parser.add_argument('--start')
    args=parser.parse_args()
    with job('kmeans') as (spark,context): run(spark,context,end=args.end,start=args.start)
