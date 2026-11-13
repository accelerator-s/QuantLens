from __future__ import annotations
import numpy as np
import pandas as pd


def settings(steps,missing='drop'):
    import json
    decoded=[]
    for step in steps:
        item=json.loads(step) if step.lstrip().startswith('{') else {'step':step.split(':',1)[0]}
        name=item['step']
        if name not in {'winsorize','zscore','industry_neutral','size_neutral','fillna'}:
            raise ValueError(f'Unsupported DWS standardization: {name}')
        if name=='winsorize' and (float(item.get('lower_pct',.01))!=.01 or float(item.get('upper_pct',.99))!=.99):
            raise ValueError('DWS winsorization uses the registered 1%/99% contract')
        if name=='fillna':
            if item['method']=='industry_median':missing='fill'
            elif item['method']=='drop':missing='drop'
            else:raise ValueError('DWS only supports drop or daily industry median missing handling')
        decoded.append(name)
    return set(decoded),missing


def standardize(frame, steps=(), missing='drop'):
    steps,missing=settings(steps,missing)
    out = frame.copy()
    values = pd.to_numeric(out['value'], errors='coerce').replace([np.inf,-np.inf], np.nan)
    industry = out.get('industry_code', pd.Series(index=out.index, dtype='object'))
    if missing == 'fill':
        values = values.fillna(values.groupby(industry).transform('median'))
    elif missing != 'drop':
        raise ValueError(f'Unknown missing_handling: {missing}')
    values = values.clip(values.quantile(.01), values.quantile(.99))
    valid = values.notna()
    design = [np.ones(len(out))]
    if 'industry_neutral' in steps:
        valid &= industry.notna()
        design.extend(pd.get_dummies(industry, dtype=float).to_numpy().T)
    if 'size_neutral' in steps:
        size = np.log(pd.to_numeric(out['total_mv'], errors='coerce').where(lambda s:s>0))
        valid &= size.notna()
        design.append(size.to_numpy())
    if len(design)>1 and valid.any():
        x = np.asarray(design).T[valid]
        y = values.loc[valid].to_numpy()
        values.loc[valid] = y-x@np.linalg.lstsq(x,y,rcond=None)[0]
    values = values.where(valid)
    deviation = values.std(ddof=0)
    out['std_value'] = (values-values.mean())/deviation if deviation and np.isfinite(deviation) else values*0
    out['coverage'] = float(valid.mean())
    return out
