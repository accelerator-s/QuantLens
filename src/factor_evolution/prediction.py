"""Cheap directional screening; never a substitute for net-return validation."""
import math


def screen(rows):
    """Screen mature labels from the caller's declared research windows.

    Positive mean IC and top-minus-bottom spread are economic direction checks,
    not p-values. Sixty dates is a data sufficiency rule, not sixty independent
    observations when labels overlap. Freeze this rule before searching.
    """
    valid = bool(rows) and all(r.get('valid_ic_days', 0) >= 60 and
        all(r.get(k) is not None and math.isfinite(float(r[k])) for k in
            ['rank_ic_mean', 'top_minus_bottom']) for r in rows)
    ic = sum(r['rank_ic_mean'] for r in rows)/len(rows) if valid else None
    spread = sum(r['top_minus_bottom'] for r in rows)/len(rows) if valid else None
    return {'version': 'directional_screen_v1',
        'passed': bool(valid and ic > 0 and spread > 0),
        'mean_rank_ic': ic, 'mean_top_minus_bottom': spread,
        'reason': '有足够成熟标签且平均预测方向为正' if valid and ic > 0 and spread > 0
                  else '成熟标签不足或预测方向未通过；跳过候选的完整回测',
        'statistical_significance_test': False}


def spark_prediction(scored):
    import numpy as np
    import pandas as pd
    def daily(pdf):
        v = pdf[['score', 'ret']].replace([np.inf, -np.inf], np.nan).dropna()
        ranks = v.score.rank(pct=True)
        return pd.DataFrame([{'date': pdf.date.iloc[0],
            'rank_ic': v.score.rank().corr(v.ret.rank()) if len(v) > 2 else np.nan,
            'spread': v.loc[ranks.gt(.8), 'ret'].mean()-v.loc[ranks.le(.2), 'ret'].mean()}])
    rows = scored.select('date', 'score', 'ret').groupBy('date').applyInPandas(
        daily, 'date date, rank_ic double, spread double').orderBy('date').toPandas()
    return {'rank_ic_mean': float(rows.rank_ic.mean()) if rows.rank_ic.notna().any() else None,
            'top_minus_bottom': float(rows.spread.mean()) if rows.spread.notna().any() else None,
            'valid_ic_days': int(rows.rank_ic.notna().sum()),
            'scope': 'mature_labels_in_declared_window', 'not_a_net_return': True}
