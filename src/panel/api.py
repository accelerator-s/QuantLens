"""Research panel contract backed by canonical DWD extracts."""
from __future__ import annotations
from collections.abc import Sequence
import pandas as pd
from exceptions import PanelCacheError
from .contract import panel_field_names,resolve_fields
from .universe import UniverseConfig,build_universe_mask


def load_panel(fields:Sequence[str]|None=None,*,start=None,end=None,universe:UniverseConfig|None=None,
               exclude_imputed=False,industry_classification_id=None,as_datetime_index=True):
    from quantlens.research_data import extract
    if start and end and start>end:raise PanelCacheError(code='PANEL_INVALID_RANGE',message='研究面板日期顺序错误')
    if industry_classification_id not in (None,'P0221'):
        raise PanelCacheError(code='PANEL_CLASSIFICATION_CONTRACT',message='行业分类由 panel_fields.yaml 固定',suggested_action='研究口径变更须修改映射并重算版本')
    names=[s.name for s in resolve_fields(list(fields) if fields is not None else None)]
    aux=['is_trading','listed_days','trading_status'] if universe else []
    frame=extract({'kind':'panel','fields':sorted(set(names+aux)),'start':start,'end':end})
    matrices={name:frame.pivot(index='date',columns='code',values=name).sort_index() for name in set(names+aux)}
    if universe:
        mask=build_universe_mask(universe,is_trading=matrices['is_trading'].fillna(False),listed_days=matrices['listed_days'],trading_status=matrices['trading_status'].astype('string'))
        matrices={name:value.where(mask) for name,value in matrices.items()}
    result={name:matrices[name] for name in names}
    if not as_datetime_index:
        for value in result.values():value.index=value.index.strftime('%Y-%m-%d')
    return result


def cache_status():
    # A single published panel; no independently computed matrix variants.
    from types import SimpleNamespace
    from quantlens.ads import manifest,query
    doc=manifest();overview=query('overview',limit=1)
    bounds=overview[0] if overview else {}
    status=SimpleNamespace(state='fresh' if doc.get('run_id') else 'missing',reasons=[] if doc.get('run_id') else ['warehouse_not_published'],meta={'build_params':{'start':bounds.get('start'),'end':bounds.get('end')},'data_snapshot':doc.get('data_snapshot'),'run_id':doc.get('run_id')})
    return {'default':status}

__all__=['load_panel','cache_status','panel_field_names']
