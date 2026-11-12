import json
import os
import time
from datetime import date

from pydantic import BaseModel, Field, model_validator
from exceptions import ContractViolation
from paths import workspace
from quantlens import data, store
from quantlens.spark_features import FEATURES, LABELS, build
from quantlens.provenance import capture

class ResearchConfig(BaseModel):
    name: str = 'A股五日收益研究'
    start: date = date(2016,1,1)
    train_end: date = date(2022,12,31)
    valid_end: date = date(2024,12,31)
    end: date = date(2025,12,31)
    stock_limit: int = Field(default=0, ge=0, le=7000)
    cores: int = Field(default=4, ge=1, le=12)
    trees: int = Field(default=30, ge=5, le=150)
    depth: int = Field(default=3, ge=2, le=6)
    top_n: int = Field(default=30, ge=5, le=100)
    cost_bps: float = Field(default=15, ge=0, le=100)
    seed: int = 42

    @model_validator(mode='after')
    def ordered(self):
        if not self.start < self.train_end < self.valid_end < self.end:
            raise ValueError('训练、验证、评估日期必须依次递增')
        return self

def start_session(cores, run_id):
    from pyspark.sql import SparkSession
    from quantlens.compute import master
    os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1')
    import sys
    os.environ['PYSPARK_PYTHON'] = sys.executable
    root = workspace().root / '.spark'
    (root/'events').mkdir(parents=True, exist_ok=True)
    return (SparkSession.builder.master(master(cores))
        .appName(f'QuantLens-{run_id}').config('spark.driver.memory','4g')
        .config('spark.sql.shuffle.partitions',str(max(8,cores*4)))
        .config('spark.default.parallelism',str(max(8,cores*4)))
        .config('spark.sql.session.timeZone','Asia/Shanghai')
        .config('spark.cores.max',str(cores)).config('spark.executor.memory','3g')
        .config('spark.eventLog.enabled','true').config('spark.eventLog.dir',str(root/'events'))
        .config('spark.local.dir',str(root/'temp')).config('spark.ui.showConsoleProgress','false')
        .getOrCreate())

def run(config, reporter, run_id=None):
    from pyspark.ml import Pipeline, PipelineModel
    from pyspark.ml.feature import Imputer, VectorAssembler
    from pyspark.ml.regression import GBTRegressor
    from pyspark.sql import functions as F
    from quantlens.evaluation import evaluate
    c = config
    if c.end.isoformat() > data.coverage()['end'][:10]:
        raise ContractViolation(code='QL_DATE_UNCOVERED',message='研究结束日期超过数据水位',suggested_action='缩短区间或先同步数据')
    run_id = run_id or store.uid('QL')
    root = store.run_path(run_id)
    root.mkdir(parents=True, exist_ok=True)
    config_path = root/'config.json'
    if config_path.exists():
        frozen = json.loads(config_path.read_text())
        if frozen['config'] != config.model_dump(mode='json'):
            raise ContractViolation(code='QL_CONFIG_FROZEN',message='恢复任务不能修改参数',suggested_action='创建新的研究任务')
        dataset_id = frozen['dataset_id']
        source = workspace().data/'snapshots'/dataset_id
    else:
        dataset_id, source = data.freeze(reporter)
        frozen = {'run_id':run_id,'dataset_id':dataset_id,'config':c.model_dump(mode='json'),
                  'created_at':store.now(),'label':'adj_close[t+6]/adj_close[t+1]-1',
                  'evaluation':'历史时间外评估，未声称此前从未观察', 'features':FEATURES,
                  'source':capture(root)}
        store.write_json(config_path,frozen)
        store.add('model_run',frozen,run_id)
    if (root/'evaluation_opened.json').exists():
        raise ContractViolation(code='QL_EVALUATION_EXPOSED',message='该评估已经开封，禁止重跑',suggested_action='查看已保存结果；失败记录保留')
    timings = {}
    spark = start_session(c.cores,run_id)
    spark.sparkContext.setLogLevel('WARN')
    runtime={'spark_version':spark.version,'master':spark.sparkContext.master,
        'application_id':spark.sparkContext.applicationId,'ui':spark.sparkContext.uiWebUrl,'state':'running'}
    store.write_json(root/'runtime.json',runtime)
    try:
        begun = time.perf_counter()
        features_path = root/'features'
        if not (features_path/'_SUCCESS').exists():
            reporter(f'计算{len(FEATURES)}项因子与五日标签')
            frame = build(spark,source,str(c.start),str(c.end),c.stock_limit)
            frame.write.mode('overwrite').parquet(str(features_path))
        df = spark.read.parquet(str(features_path))
        timings['features_seconds'] = round(time.perf_counter()-begun,2)
        useful = df.where(F.col('eligible')).replace(float('inf'),None,FEATURES).replace(float('-inf'),None,FEATURES)
        train = useful.where((F.col('date')<=F.lit(str(c.train_end))) & (F.col('label_end')<=F.lit(str(c.train_end))) & F.col('label').isNotNull())
        valid = useful.where((F.col('date')>F.lit(str(c.train_end))) & (F.col('date')<=F.lit(str(c.valid_end))) & (F.col('label_end')<=F.lit(str(c.valid_end))) & F.col('label').isNotNull())
        test = useful.where((F.col('date')>F.lit(str(c.valid_end))) & F.col('label').isNotNull())
        counts = {'train':train.count(),'validation':valid.count(),'test':test.count(),'features':df.count()}
        if min(counts[k] for k in ['train','validation','test'])<100:
            raise ContractViolation(code='QL_SAMPLES_INSUFFICIENT',message='每个区间至少需要100个有效样本',suggested_action='扩大日期或证券范围')
        reporter(f'有效样本：{counts}')
        from quantlens.selection import select
        selection_path=root/'selection.json'
        selection=json.loads(selection_path.read_text()) if selection_path.exists() else select(root,c,reporter)
        selected=selection['selected']
        begun = time.perf_counter()
        model_path = root/'model'
        if (model_path/'metadata'/'_SUCCESS').exists():
            model = PipelineModel.load(str(model_path))
        else:
            imputed = [f'{f}_filled' for f in selected]
            pipeline = Pipeline(stages=[Imputer(inputCols=selected,outputCols=imputed,strategy='median'),
                VectorAssembler(inputCols=imputed,outputCol='features'),
                GBTRegressor(featuresCol='features',labelCol='label',maxIter=c.trees,maxDepth=c.depth,
                             stepSize=0.05,seed=c.seed,subsamplingRate=0.8)])
            reporter('训练MLlib梯度提升树')
            model = pipeline.fit(train)
            model.write().overwrite().save(str(model_path))
        timings['training_seconds'] = round(time.perf_counter()-begun,2)
        reporter('验证误差与区间校准')
        validation = model.transform(valid)
        residual_q = validation.select(F.abs(F.col('label')-F.col('prediction')).alias('residual')).approxQuantile('residual',[0.9],0.001)[0]
        importance = [{'feature':name,'label':label,'importance':float(v)} for name,label,v in
                      zip(selected,[dict(zip(FEATURES,LABELS))[f] for f in selected],model.stages[-1].featureImportances)]
        model.transform(useful).select(*df.columns,'prediction').write.mode('overwrite').parquet(str(root/'predictions'))
        store.write_json(root/'calibration.json',{'absolute_error_q90':residual_q,'source':'validation',
            'note':'验证期残差区间；市场变化后覆盖率可能下降','importance':importance})
        store.write_json(root/'evaluation_opened.json',{'opened_at':store.now(),'dataset_id':dataset_id})
        store.add('evaluation_exposure',{'run_id':run_id,'start':str(c.valid_end),'end':str(c.end)})
        reporter('计算排序、分组、成本与回撤')
        begun = time.perf_counter()
        result = evaluate(root,c,residual_q)
        timings['evaluation_seconds'] = round(time.perf_counter()-begun,2)
        result.update({'run_id':run_id,'dataset_id':dataset_id,'counts':counts,'timings':timings,
                       'importance':importance,'state':'completed','completed_at':store.now()})
        store.write_json(root/'metrics.json',result)
        store.add('model_result',result)
        store.write_json(root/'runtime.json',{**runtime,'state':'completed','finished_at':store.now()})
        reporter('研究完成，结果已保存')
        return result
    except Exception:
        store.write_json(root/'runtime.json',{**runtime,'state':'failed','finished_at':store.now()})
        raise
    finally:
        spark.stop()

def resume(run_id, reporter):
    value = json.loads((store.run_path(run_id)/'config.json').read_text())
    return run(ResearchConfig(**value['config']),reporter,run_id)
