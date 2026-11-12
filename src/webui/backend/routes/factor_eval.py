from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from ai.schemas import validate_ai_output
from exceptions import ContractViolation
from factors import FactorMetadata, FactorPlugin, FactorRegistry, FactorStatus, run_candidate
from factors.registry import hash_code
from paths import workspace

from ..schemas import confirm_preview, parse_enum
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

def _check_factor_issue(issue_id: str) -> None:
    from issues import IssueStore, IssueType
    from issues import Status as IssueStatus

    issue, _ = IssueStore().load(issue_id)
    if issue.type != IssueType.FACTOR:
        raise ContractViolation(
            code="WEBUI_FACTOR_ISSUE_TYPE",
            message=f"issue {issue_id} 类型为 {issue.type.value}，需要 factor 类型 issue。",
        )
    if issue.status == IssueStatus.OPEN:
        raise ContractViolation(
            code="WEBUI_FACTOR_ISSUE_OPEN",
            message=f"issue {issue_id} 仍在 open 状态，先推进到 in_review/accepted/solved 再注册。",
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
    issue_id: str
    override: bool = False

@router.post("/factors/candidate/register", include_in_schema=False)
def candidate_register(payload: CandidateRegisterPayload) -> dict:
    from ai.execution import syntax_check

    _, candidate = _parse_candidate_yaml(payload.yaml_text)
    _check_panel_dependencies(list(candidate.dependencies))
    _check_factor_issue(payload.issue_id)

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
                "关联事项": payload.issue_id,
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
        production_permission=False,
        evaluation_metrics=list(candidate.evaluation_metric_hints),
        status=FactorStatus.DRAFT,
        code_hash=hash_code(candidate.code),
        plugin_path=str(plugin_path.relative_to(plugin.ws.root)),
        related_issue_id=payload.issue_id,
    )
    registry.upsert(metadata, override=payload.override)
    return json_safe({"factor": registry.get(candidate.factor_name)})

def _parse_int_list(raw: str, *, field: str) -> list[int]:
    try:
        return [int(x) for x in raw.split(",") if x.strip()]
    except ValueError as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_INT_LIST",
            message=f"{field} 须为逗号分隔的整数：{raw!r}。",
        ) from exc

def _parse_neutralizations(raw: str) -> list:
    from factors.evaluation import Neutralization

    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if tokens == ["none"]:
        return []
    return [parse_enum(Neutralization, t, "neutralize") for t in tokens]

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
    neutralize: str = "industry"
    groups: str = "5,10"
    horizons: str = "1,2,3,5,10,20"
    horizon: int = 1
    min_listed_days: int = 120
    min_cross_section: int = 30
    limit_filter: bool = True
    strict_compare: bool = False
    lookback_buffer: int = 250
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
            decay_horizons=_parse_int_list(payload.horizons, field="horizons"),
            group_counts=_parse_int_list(payload.groups, field="groups"),
            neutralizations=_parse_neutralizations(payload.neutralize),
            min_listed_days=payload.min_listed_days,
            min_cross_section=payload.min_cross_section,
            exclude_limit_locked_entry=payload.limit_filter,
            strict_compare=payload.strict_compare,
            lookback_buffer_days=payload.lookback_buffer,
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
                "操作": "因子横截面评价（本地执行）",
                "因子": name,
                "评价窗口": f"{eval_start} ~ {eval_end}",
                "收益口径": payload.return_field,
                "中性化": payload.neutralize,
                "分组 / 持有期": f"{payload.groups} / {payload.horizons}（核心 {payload.horizon}）",
                "续算": resume or "（新运行）",
                "说明": "评价为分钟级任务，确认后在后台执行，可在页面轮询进度。",
            }
        )

    def run(append_log) -> dict:
        outcome = evaluate_factor(config, resume_run_id=resume, on_progress=append_log)
        return {"run_id": outcome.run_id, "run_dir": str(outcome.run_dir)}

    task_id = manager.start(f"factor-evaluate:{name}", run, resource=f"factor-eval:{name}")
    return {"task_id": task_id}

def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def _eval_run_dir(name: str, run_id: str) -> Path:
    FactorRegistry().get(name)
    base = (workspace().runs / "factor_eval" / name).resolve()
    run_dir = (base / run_id).resolve()
    if run_dir.parent != base or not run_dir.is_dir():
        raise ContractViolation(
            code="WEBUI_EVAL_RUN_NOT_FOUND",
            message=f"因子 {name} 的评价运行 {run_id} 不存在。",
        )
    return run_dir

def _headline_metrics(metrics: dict) -> dict:
    
    labels = metrics.get("labels") or {}
    output: dict[str, dict] = {}
    for label, payload in labels.items():
        variants = payload.get("variants") or {}
        selected_name = next(
            (
                name
                for name in ("industry_size", "industry", "size", "raw")
                if name in variants and (variants[name].get("ic") or {}).get("n_valid_days")
            ),
            "",
        )
        if not selected_name:
            continue
        selected = variants[selected_name]
        groups = selected.get("groups") or {}
        largest_group = sorted(
            groups,
            key=lambda value: int(str(value).removeprefix("g")),
        )[-1] if groups else ""
        long_short = (
            (groups.get(largest_group) or {}).get("long_short") or {}
        )
        output[label] = {
            "variant": selected_name,
            "ic_mean": (selected.get("ic") or {}).get("ic_mean"),
            "icir_annualized": (selected.get("ic") or {}).get("icir_annualized"),
            "win_rate": (selected.get("ic") or {}).get("win_rate"),
            "valid_days": (selected.get("ic") or {}).get("n_valid_days"),
            "long_short_annualized": long_short.get("annualized_return"),
            "max_drawdown": long_short.get("max_drawdown"),
            "turnover_oneside": long_short.get("avg_daily_turnover_oneside"),
        }
    return output

@router.get("/factors/{name}/eval-runs")
def list_eval_runs(name: str) -> dict:
    FactorRegistry().get(name)
    base = workspace().runs / "factor_eval" / name
    items = []
    if base.is_dir():
        for run_dir in sorted(base.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            status = _read_yaml(run_dir / "status.yaml")
            cfg = _read_yaml(run_dir / "config.yaml")
            config = cfg.get("config") or {}
            metrics = _read_yaml(run_dir / "metrics.yaml")
            headline = _headline_metrics(metrics)
            
            preferred = headline.get("strict") or headline.get("default") or {}
            items.append(
                {
                    "run_id": run_dir.name,
                    "state": status.get("state", ""),
                    "updated_at": status.get("updated_at", ""),
                    "created_at": cfg.get("created_at", ""),
                    "period": f"{config.get('start', '')} ~ {config.get('end', '')}",
                    "return_field": config.get("return_field", ""),
                    "primary_horizon": config.get("primary_horizon"),
                    "has_metrics": bool(metrics),
                    "headline": headline,
                    **preferred,
                }
            )
    return json_safe({"factor": name, "items": items})

@router.get("/factors/{name}/eval-runs/{run_id}")
def get_eval_run(name: str, run_id: str) -> dict:
    run_dir = _eval_run_dir(name, run_id)
    charts_dir = run_dir / "charts"
    charts = sorted(p.name for p in charts_dir.iterdir()) if charts_dir.is_dir() else []
    return json_safe(
        {
            "factor": name,
            "run_id": run_id,
            "status": _read_yaml(run_dir / "status.yaml"),
            "config": _read_yaml(run_dir / "config.yaml"),
            "metrics": _read_yaml(run_dir / "metrics.yaml"),
            "charts": charts,
        }
    )

@router.get("/factors/{name}/eval-runs/{run_id}/charts/{filename}")
def get_eval_chart(name: str, run_id: str, filename: str) -> FileResponse:
    run_dir = _eval_run_dir(name, run_id)
    charts_dir = run_dir / "charts"
    
    valid = {p.name for p in charts_dir.iterdir()} if charts_dir.is_dir() else set()
    if filename not in valid:
        raise ContractViolation(
            code="WEBUI_CHART_NOT_FOUND",
            message=f"图表文件 {filename!r} 不存在。",
        )
    return FileResponse(charts_dir / filename, media_type="text/csv")
