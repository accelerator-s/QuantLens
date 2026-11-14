from __future__ import annotations
import json
from factors.registry import FactorRegistry, hash_code
from factors.plugins import FactorPlugin
from quantlens.warehouse import job, lineage, publish
from exceptions import ContractViolation


def metadata(spark):
    existing = {}
    if spark.catalog.tableExists('dws.factor_meta'):
        existing = {(r.name,r.version):(r.code_hash,json.loads(r.metadata)) for r in spark.table('dws.factor_meta').select('name','version','code_hash','metadata').collect()}
    result = []
    for meta in FactorRegistry().list(include_research=True):
        if meta.name == 'model_gbt':
            from quantlens.spark_engine import identity
            names=list(meta.dependencies)
            code_hash=identity(names)
        else:
            code_hash = hash_code(FactorPlugin.default().read(meta.name))
        old = existing.get((meta.name,meta.version))
        identity_fields=('parameters','dependencies','eligibility','standardization','missing_handling','direction','min_history','engine')
        changed=old and (old[0]!=code_hash or any(old[1].get(key,{} if key=='eligibility' else None)!=meta.model_dump(mode='json').get(key) for key in identity_fields))
        if changed:
            raise ContractViolation(code='FACTOR_VERSION_IMMUTABLE',message=f'{meta.name}/{meta.version} 计算定义变化，必须使用新版本',suggested_action='为修改后的因子注册新版本')
        record={**meta.model_dump(mode='json'),'code_hash':code_hash}
        if meta.name=='model_gbt':record['feature_versions']={name:FactorRegistry().get(name).version for name in meta.dependencies}
        result.append(record)
    return result


def run(spark, context):
    rows = [(m['name'], m['version'], m['code_hash'], m['display_name'], m['direction'], m['status'],json.dumps(m,ensure_ascii=False)) for m in metadata(spark)]
    frame = spark.createDataFrame(rows, 'name string, version string, code_hash string, display_name string, direction string, status string, metadata string')
    # Preserve older metadata versions; the registry is the sole authoring source.
    if spark.catalog.tableExists('dws.factor_meta'):
        old = spark.table('dws.factor_meta').select(*frame.columns)
        frame = old.join(frame.select('name','version'), ['name','version'],'left_anti').unionByName(frame)
    publish(spark, lineage(frame, context), 'dws.factor_meta', run=context)

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--batch');args=parser.parse_args()
    with job('factor_meta') as (spark, context):
        run(spark, context)
        if args.batch:
            from quantlens.spark_ads import export
            export(spark,context,args.batch)
