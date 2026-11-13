"""Run through the same Spark/host training bridge used by front-end jobs."""
import json
from pathlib import Path

from quantlens.warehouse import job
from quantlens.gpu import ClusterModel, PCAModel, fit_tree, request, use_gpu
from quantlens.spark_weight_fit import fit

with job('gpu_training_smoke') as (spark, context):
    from pyspark.sql import functions as F
    from datetime import date, timedelta
    import numpy as np
    random = np.random.default_rng(42)
    rows = []
    for i in range(2400):
        a,b,c = random.normal(size=3)
        rows.append((f'{i % 120:06d}',date(2025,1,1)+timedelta(days=i//120),float(a),float(b),float(c),float(2*a-b+.02*c)))
    frame = spark.createDataFrame(rows,'code string,date date,a double,b double,c double,ret double').cache()
    names = ['a','b','c']
    assert use_gpu(), 'CUDA was not selected'
    linear = fit(spark,frame,names,'model_linear')
    assert linear[0] > .6 and linear[1] < -.3, linear
    logistic = fit(spark,frame,names,'model_logistic')
    assert logistic[0] > .5 and logistic[1] < -.2, logistic
    tree = fit_tree(frame,names)
    predictions = tree.transform(frame)
    mse = predictions.select(F.avg((F.col('prediction')-F.col('ret'))**2).alias('mse')).first().mse
    assert mse < .3, mse
    pca = PCAModel(request('pca',train=frame,features=names))
    assert pca.transform(frame).count() == len(rows)
    clustered = request('kmeans',train=frame,features=names)
    for candidate in clustered['candidates']:
        model = ClusterModel(candidate,names)
        assert model.transform(frame).select('prediction').where((F.col('prediction')<0)|(F.col('prediction')>=candidate['k'])).count() == 0
    result={'cuda':True,'linear':linear.tolist(),'logistic':logistic.tolist(),'tree_mse':mse,'pca_components':len(pca.explainedVariance),'kmeans_k':[item['k'] for item in clustered['candidates']],**context}
    Path('/data/ads/_training/smoke-result.json').write_text(json.dumps(result))
    print('QUANTLENS_GPU_SMOKE='+json.dumps(result),flush=True)
