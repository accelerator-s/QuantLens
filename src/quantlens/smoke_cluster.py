"""Execute real HDFS/YARN/Hive/Arrow checks, retaining measured evidence."""
import json,time
from quantlens.warehouse import job,lineage,publish,root_uri,write_json


def run(spark,context):
    from pyspark.sql import functions as F
    results={}
    words=spark.sparkContext.parallelize(['factor research factor','hadoop spark'],2).flatMap(str.split).map(lambda word:(word,1)).reduceByKey(lambda a,b:a+b).collectAsMap()
    assert words['factor']==2
    results['yarn_wordcount']=words
    frame=spark.createDataFrame([('000001',1.,2026),('000002',2.,2026)],'code string,value double,year int')
    info=publish(spark,lineage(frame,context),'meta.smoke',['year'],run=context)
    assert spark.table('meta.smoke').count()==2
    results['hive_spark_shared_table']=info
    arrow=frame.groupBy('year').applyInPandas(lambda pdf:pdf,'code string,value double,year int')
    assert arrow.count()==2
    results['arrow_pandas']=True
    results['spark_version']=spark.version
    results['application_id']=spark.sparkContext.applicationId
    write_json(spark,f'{root_uri()}/meta/smoke/{context["run_id"]}',results)
    print('QUANTLENS_SMOKE='+json.dumps(results,default=str))
    return results

if __name__=='__main__':
    with job('smoke') as (spark,context): run(spark,context)
