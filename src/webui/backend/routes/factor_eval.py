from __future__ import annotations

import yaml
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ai.schemas import validate_ai_output
from exceptions import ContractViolation
from factors import FactorMetadata, FactorPlugin, FactorRegistry, FactorStatus, run_candidate
from factors.registry import hash_code
from paths import workspace

from ..schemas import confirm_preview
from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=["factor-eval"])

def _parse_candidate_yaml(yaml_text: str):
    try:
        raw = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_YAML",
            message=f"候选 YAML 解析失败：{exc}",
        ) from exc
    if not isinstance(raw, dict):
        raise ContractViolation(code="WEBUI_INVALID_YAML", message="候选 YAML 顶层必须是映射。")
    raw.setdefault("task", "factor_candidate")
    raw.setdefault("model_name", raw.get("model_name") or "manual")
    candidate = validate_ai_output("factor_candidate", raw)
    return raw, candidate

def _check_panel_dependencies(dependencies: list[str]) -> None:
    from panel import panel_field_names

    unknown = sorted(set(dependencies) - set(panel_field_names()))
    if unknown:
        raise ContractViolation(
            code="WEBUI_FACTOR_DEPENDENCY_UNKNOWN",
            message=f"因子 dependencies 含面板契约之外的字段：{'、'.join(unknown)}。",
            suggested_action="在因子库查看可用字段。",
        )

def _panel_payload_from_yaml(panel_yaml: str) -> dict:
    if not panel_yaml.strip():
        return {}
    raw = yaml.safe_load(panel_yaml) or {}
    if not isinstance(raw, dict):
        raise ContractViolation(code="WEBUI_INVALID_YAML", message="panel YAML 顶层必须是映射。")
    import pandas as pd

    payload: dict = {}
    for field, value in raw.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            df = pd.DataFrame(value)
            idx = next((c for c in ("date", "datetime", "index") if c in df.columns), None)
            if idx is not None:
                df = df.set_index(idx)
            payload[field] = df
        else:
            payload[field] = value
    return payload

class CandidateCheckPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml_text: str
    panel_yaml: str = ""

@router.post("/factors/candidate/check", include_in_schema=False)
def candidate_check(payload: CandidateCheckPayload) -> dict:
    raw, candidate = _parse_candidate_yaml(payload.yaml_text)
    _check_panel_dependencies(list(candidate.dependencies))
    panel_payload = _panel_payload_from_yaml(payload.panel_yaml)
    def run(append_log):
        append_log(f'执行候选 {candidate.factor_name}')
        report = run_candidate(raw, panel_payload=panel_payload)
        return {'factor_name':report.factor_name, 'ok':True, 'output_shape':report.output_shape}
    return {'task_id':manager.start(f'factor-candidate-check:{candidate.factor_name}', run)}

class CandidateRegisterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    yaml_text: str
    override: bool = False

@router.post("/factors/candidate/register", include_in_schema=False)
def candidate_register(payload: CandidateRegisterPayload) -> dict:
    from ai.execution import syntax_check

    _, candidate = _parse_candidate_yaml(payload.yaml_text)
    _check_panel_dependencies(list(candidate.dependencies))

    errs = syntax_check(candidate.code)
    if errs:
        raise ContractViolation(
            code="WEBUI_FACTOR_SYNTAX_INVALID",
            message="静态检查失败：" + "；".join(errs),
        )

    registry = FactorRegistry()
    exists = any(f.name == candidate.factor_name for f in registry.list())
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "注册因子（DRAFT）",
                "因子": candidate.factor_name,
                "方向": candidate.direction,
                "时间尺度": candidate.timescale,
                "依赖": "、".join(candidate.dependencies) or "（无）",
                "同名覆盖": ("是" if payload.override else "否")
                + ("；数据表已存在同名因子" if exists else ""),
                "说明": "注册写入数据表与插件目录，状态 DRAFT，不自动获得生产权限。",
            }
        )

    plugin = FactorPlugin.default()
    plugin_path = plugin.write(candidate.factor_name, candidate.code, override=payload.override)

    from session import has_active_market, load_session

    session = load_session()
    if has_active_market(session):
        markets = [session.active_market]
        instruments = [session.active_instrument_class]
    else:
        markets = []
        instruments = []

    metadata = FactorMetadata(
        name=candidate.factor_name,
        display_name=candidate.display_name or candidate.factor_name,
        assumption=candidate.assumption,
        applicable_markets=markets,
        applicable_instruments=instruments,
        timescale=candidate.timescale,
        dependencies=list(candidate.dependencies),
        output_frequency=candidate.timescale,
        direction=candidate.direction,
        parameters=candidate.parameters,
        standardization=list(candidate.standardization),
        missing_handling=candidate.missing_handling,
        ai_generated=True,
        evaluation_metrics=list(candidate.evaluation_metric_hints),
        status=FactorStatus.DRAFT,
        code_hash=hash_code(candidate.code),
        plugin_path=str(plugin_path.relative_to(plugin.ws.root)),
    )
    registry.upsert(metadata, override=payload.override)
    return json_safe({"factor": registry.get(candidate.factor_name)})

def _default_eval_range(start: str, end: str) -> tuple[str, str]:
    if start and end:
        return start, end
    from panel import cache_status

    meta = cache_status()["default"].meta or {}
    params = meta.get("build_params") or {}
    cache_start, cache_end = params.get("start"), params.get("end")
    if (start or cache_start) is None or (end or cache_end) is None:
        raise ContractViolation(
            code="WEBUI_EVAL_RANGE_MISSING",
            message="计算缓存不存在且未显式给出评价窗口。",
            suggested_action="先在数据页重建计算缓存，或显式填写起止日期。",
        )
    return start or cache_start, end or cache_end

class EvaluatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    start: str = ""
    end: str = ""
    return_field: str = "ret_wd"
    horizon: int = 5
    resume: str = ""

@router.post("/factors/{name}/evaluate")
def evaluate_factor_endpoint(name: str, payload: EvaluatePayload) -> dict:
    from factors.evaluation import EvaluationConfig, evaluate_factor

    FactorRegistry().get(name)
    eval_start, eval_end = _default_eval_range(payload.start.strip(), payload.end.strip())
    try:
        config = EvaluationConfig(
            factor_name=name,
            start=eval_start,
            end=eval_end,
            return_field=payload.return_field,  
            primary_horizon=payload.horizon,

        )
    except ValueError as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_EVAL_CONFIG",
            message=f"评价配置校验失败：{exc}",
        ) from exc

    resume = payload.resume.strip() or None
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "因子横截面评价（Spark）",
                "因子": name,
                "评价窗口": f"{eval_start} ~ {eval_end}",
                "收益口径": payload.return_field,
                "标准化": "读取因子注册表已发布的 DWS 标准化值",
                "分组 / 持有期": f"5 分组 / 1、5、10、20 日（核心 {payload.horizon}）",
                "续算": resume or "（新运行）",
                "说明": "读取已发布因子和成熟标签，确认后在后台提交 Spark 作业。",
            }
        )

    def run(append_log) -> dict:
        outcome = evaluate_factor(config, resume_run_id=resume, on_progress=append_log)
        return {"run_id": outcome.run_id, "run_dir": str(outcome.run_dir)}

    task_id = manager.start(f"factor-evaluate:{name}", run, resource=f"factor-eval:{name}")
    return {"task_id": task_id}

@router.get('/factors/{name}/eval-runs')
def list_eval_runs(name:str):
    from quantlens import ads
    rows=ads.query('factor_eval','factor=?',[name],limit=10000,order='as_of DESC,eval_end DESC,horizon')
    groups={}
    for row in rows:groups.setdefault(row['run_id'],[]).append(row)
    items=[]
    for run_id,results in groups.items():
        row=next((item for item in results if item['horizon']==5),results[0])
        items.append({'run_id':run_id,'state':'completed','updated_at':row['as_of'],'created_at':row['as_of'],
                      'period':str(row['eval_start'])+' ~ '+str(row['eval_end']),'return_field':'ret_wd','primary_horizon':row['horizon'],
                      'has_metrics':True,'variant':'published','ic_mean':row['rank_ic'],
                      'icir_annualized':row['icir']*244**.5 if row.get('icir') is not None else None})
    return {'factor':name,'items':items,**ads.provenance('factor_eval')}


@router.get('/factors/{name}/eval-runs/{run_id}')
def get_eval_run(name:str,run_id:str):
    from fastapi import HTTPException
    from quantlens import ads
    from quantlens.evaluation_view import view
    rows=ads.query('factor_eval','factor=? AND run_id=?',[name,run_id],limit=1000,order='eval_end DESC,horizon')
    if not rows:raise HTTPException(404,'评价结果尚未发布')
    row=rows[0]
    rows=[item for item in rows if item['eval_start']==row['eval_start'] and item['eval_end']==row['eval_end']]
    daily=ads.query('factor_eval_daily','factor=? AND version=? AND run_id=? AND horizon=5',[name,row['version'],run_id],limit=10000,order='date')
    metadata=ads.query('factor_meta','name=? AND version=?',[name,row['version']],limit=1)
    direction=metadata[0]['direction'] if metadata else FactorRegistry().get(name).direction
    return json_safe({'factor':name,'run_id':run_id,'status':{'state':'completed'},
                      'config':{'config':{'factor_name':name,'start':row['eval_start'],'end':row['eval_end'],'return_field':'ret_wd','primary_horizon':5}},
                      'metrics':view(rows,daily,direction=direction),'charts':['daily.csv'] if daily else [],**ads.provenance('factor_eval')})


@router.get('/factors/{name}/eval-runs/{run_id}/charts/{filename}')
def get_eval_chart(name:str,run_id:str,filename:str):
    from fastapi import HTTPException,Response
    from quantlens import ads
    import pandas as pd
    if filename!='daily.csv':raise HTTPException(404,'结果数据不存在')
    rows=ads.query('factor_eval_daily','factor=? AND run_id=? AND horizon=5',[name,run_id],limit=10000,order='date')
    if not rows:raise HTTPException(404,'结果数据尚未发布')
    return Response(pd.DataFrame(rows).to_csv(index=False),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="daily.csv"'})
