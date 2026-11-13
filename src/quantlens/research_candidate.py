"""Publish research candidates once; evaluations only consume DWS values."""
import hashlib,json
from factors.registry import FactorRegistry,hash_code
from factors.plugins import FactorPlugin


def publish_candidate(base_name,version):
    from quantlens.compute import submit
    registry=FactorRegistry();base=registry.get(base_name)
    payload={key:getattr(version,key) for key in ['code','parameters','dependencies','standardization','missing_handling','direction']}
    payload['eligibility']=base.eligibility
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:12]
    name=base_name+'_r_'+digest
    plugin=FactorPlugin.default()
    try:meta=registry.get(name)
    except Exception as exc:
        if getattr(exc,'code','')!='FACTOR_NOT_FOUND':raise
        plugin.write(name,version.code)
        meta=base.model_copy(update={'name':name,'version':version.version_id,'code_hash':hash_code(version.code),
            'plugin_path':f'factors/{name}.py','parameters':version.parameters,'standardization':version.standardization,
            'missing_handling':version.missing_handling,'direction':version.direction,'dependencies':version.dependencies,'production_permission':False})
        registry.upsert(meta)
    submit('spark_factor_values','--factor',name)
    return name


def prepare_profile(profile,runtime_factors):
    from types import SimpleNamespace
    refs=[]
    for ref in profile.factors.items:
        if ref.name not in runtime_factors:refs.append(ref);continue
        runtime=runtime_factors[ref.name]
        base=FactorRegistry().get(ref.name)
        candidate=SimpleNamespace(code=runtime.source,parameters=runtime.parameters,standardization=list(runtime.standardization) if runtime.standardization is not None else base.standardization,
            missing_handling=runtime.missing_handling or base.missing_handling,direction=runtime.direction,dependencies=list(runtime.dependencies),
            version_id='research_'+hash_code(runtime.source))
        name=publish_candidate(ref.name,candidate)
        refs.append(ref.model_copy(update={'name':name,'direction_override':''}))
    return profile.model_copy(update={'factors':profile.factors.model_copy(update={'items':refs})})

