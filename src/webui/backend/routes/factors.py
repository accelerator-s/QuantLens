from __future__ import annotations

from fastapi import APIRouter

from ai.execution import syntax_check
from factors.plugins import FactorPlugin
from factors.registry import FactorRegistry, FactorStatus, research_base_name
from paths import workspace
from presentation import factor_display_name
from trash import TrashStore

from ..schemas import DecidePayload, TransitionPayload, confirm_preview, parse_enum
from ..serialize import json_safe

router = APIRouter(tags=["factors"])

@router.get("/factors")
def list_factors(status: str = "", market: str = "", instrument_class: str = "") -> dict:
    import json
    selected_status = parse_enum(FactorStatus, status, "status") if status else None
    from quantlens import ads
    items=[]
    published={}
    for row in ads.query('factor_meta',limit=10000):
        meta=json.loads(row['metadata'])
        published[(meta['name'],meta['version'])]=row
    for meta in FactorRegistry().list(status=selected_status):
        item=meta.model_dump(mode='json')
        if research_base_name(item['name']) is not None:continue
        if market and item.get('applicable_markets') and market not in item['applicable_markets']: continue
        if instrument_class and item.get('applicable_instruments') and instrument_class not in item['applicable_instruments']: continue
        row=published.get((item['name'],item['version']))
        item.update({key:row[key] if row else None for key in ['run_id','data_snapshot']})
        if row:item['code_hash']=row['code_hash']
        metrics=ads.query('factor_eval','factor=? AND version=?',[item['name'],item['version']],limit=10,order='as_of DESC,horizon')
        item['evaluation']=metrics
        items.append(item)
    return {'items':items,'statuses':[s.value for s in FactorStatus],**ads.provenance('factor_meta')}

@router.get("/factors/{name}")
def get_factor(name: str) -> dict:
    factor = FactorRegistry().get(name)
    code = ""
    code_path = ""
    if factor.plugin_path:
        path = workspace().root / factor.plugin_path
        code_path = str(path)
        if path.exists():
            code = path.read_text(encoding="utf-8")
    payload = json_safe(
        {
            "factor": factor,
            "code": code,
            "code_path": code_path,
        }
    )
    payload["factor"]["display_name"] = factor.display_name or factor_display_name(factor.name)
    from quantlens import ads
    payload.update(ads.provenance("factor_meta"))
    payload["evaluation"] = ads.query("factor_eval", "factor=? AND version=?", [name, factor.version], order="as_of DESC")
    return payload

@router.post("/factors/{name}/transition")
def transition_factor(name: str, payload: TransitionPayload) -> dict:
    registry = FactorRegistry()
    target = parse_enum(FactorStatus, payload.target, "target")
    factor = registry.transition(name, target=target, confirm=True)
    return json_safe({"factor": factor})

@router.post("/factors/{name}/remove")
def remove_factor(name: str, payload: DecidePayload) -> dict:
    registry = FactorRegistry()
    factor = registry.get(name)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "删除因子",
                "因子": factor.name,
            }
        )
    entry = TrashStore().remove_factor(name, deleted_by=payload.decided_by or "webui", reason=payload.note)
    return {"removed": name, "trash_bucket": entry.bucket_dir.name}

@router.post("/factors/{name}/code-check")
def check_factor_code(name: str) -> dict:
    factor = FactorRegistry().get(name)
    code = FactorPlugin.default().read(name)
    errors = syntax_check(code)
    return json_safe(
        {
            "factor": factor.name,
            "code_hash": factor.code_hash,
            "ok": not errors,
            "errors": errors,
        }
    )
