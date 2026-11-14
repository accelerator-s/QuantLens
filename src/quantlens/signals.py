"""One composite-signal implementation for research, backtest and portfolio."""
from factors.registry import FactorRegistry
from backtest.signal import _resolve_direction
from exceptions import ContractViolation


def composite(spark,profile,start,end):
    from pyspark.sql import functions as F,Window
    registry=FactorRegistry();result=None;metadata={}
    method=profile.factors.weighting.value
    strategy_id=profile.extra.get('weight_strategy_id',profile.profile_name)
    for index,ref in enumerate(profile.factors.items):
        meta=registry.get(ref.name)
        factor_version=profile.extra.get('factor_versions',{}).get(ref.name,meta.version)
        direction=_resolve_direction(ref.name,ref.direction_override,meta.direction)
        frame=spark.table('dws.factor_values').where((F.col('factor')==ref.name)&(F.col('version')==factor_version)&(F.col('date')>=start)&(F.col('date')<=end)&F.col('std_value').isNotNull())
        evidence=frame.select('code_hash','data_snapshot','run_id').distinct().collect()
        if not evidence:
            raise ContractViolation(code='FACTOR_VALUES_NOT_PUBLISHED',message=f'{ref.name}/{meta.version} 在请求区间没有已发布因子值',suggested_action='先发布该版本的因子分区')
        metadata[ref.name]={'version':factor_version,'direction':direction,'code_hashes':sorted({r.code_hash for r in evidence}),
                            'data_snapshots':sorted({r.data_snapshot for r in evidence}),'value_run_ids':sorted({r.run_id for r in evidence})}
        if ref.weight_override is not None:frame=frame.withColumn('_weight',F.lit(float(ref.weight_override)))
        elif method=='equal':frame=frame.withColumn('_weight',F.lit(1/len(profile.factors.items)))
        else:
            weights=spark.table('dws.factor_weights').where((F.col('strategy_id')==strategy_id)&(F.col('factor')==ref.name)&(F.col('factor_version')==factor_version)&(F.col('method')==method)&(F.col('train_end')<F.col('as_of'))&(F.col('as_of')<=F.lit(end))).select('as_of','weight',F.col('run_id').alias('_weight_run'))
            frame=frame.join(F.broadcast(weights),F.col('date')>=F.col('as_of'))
            window=Window.partitionBy('code','date','factor','version').orderBy(F.col('as_of').desc())
            frame=frame.withColumn('_choice',F.row_number().over(window)).where('_choice=1').withColumn('_weight',F.col('weight'))
            if not frame.take(1):
                raise ContractViolation(code='STRATEGY_WEIGHTS_NOT_PUBLISHED',message=f'{strategy_id}/{method} 没有覆盖请求区间的成熟权重',suggested_action='按该策略的因子清单先完成滚动权重拟合')
            metadata[ref.name]['weight_run_ids']=[r._weight_run for r in weights.select('_weight_run').distinct().collect()]
        part=frame.select('code','date',(F.col('std_value')*direction*F.col('_weight')).alias(f'_score{index}'),F.abs('_weight').alias(f'_mass{index}'))
        result=part if result is None else result.join(part,['code','date'])
    universe=spark.table('dwd.universe').where('in_universe').select('code','date')
    result=result.join(universe,['code','date'])
    total=sum(F.col(f'_score{i}') for i in range(len(profile.factors.items)))
    mass=sum(F.col(f'_mass{i}') for i in range(len(profile.factors.items)))
    return result.select('code','date',F.when(mass>0,total/mass).alias('score')),metadata
