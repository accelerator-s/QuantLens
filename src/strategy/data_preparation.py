"""Materialize selected research factors before reading their exposures."""
from __future__ import annotations

import json

from exceptions import ContractViolation


def missing_window(calendar, published, *, min_history, start, end):
    days = sorted({str(day)[:10] for day in calendar})[min_history:]
    present = {str(day)[:10] for day in published}
    missing = [day for day in days if start <= day <= end and day not in present]
    return (missing[0], missing[-1]) if missing else None


def ensure_factor_values(spark, context, profile, start, end):
    from pyspark.sql import functions as F
    from factors.registry import FactorRegistry
    from quantlens import spark_factor_values, sync_factor_meta

    registry = FactorRegistry()
    versions = profile.extra['factor_versions']
    calendar = [row.date for row in spark.table('dwd.prices_clean')
                .where(F.col('date') <= end).select('date').distinct().orderBy('date').collect()]
    names = [ref.name for ref in profile.factors.items]
    published = {name: [] for name in names}
    if spark.catalog.tableExists('dws.factor_values'):
        rows = (spark.table('dws.factor_values')
                .where(F.col('factor').isin(names) & F.col('date').between(start, end)
                       & F.col('std_value').isNotNull())
                .select('factor', 'version', 'date').distinct().collect())
        for row in rows:
            if row.version == versions[row.factor]:
                published[row.factor].append(row.date)
    pending = []
    for name in names:
        meta = registry.get(name)
        window = missing_window(calendar, published[name], min_history=meta.min_history,
                                start=start, end=end)
        if window:
            if meta.version != versions[name]:
                raise ContractViolation(code='STRATEGY_FACTOR_VERSION_UNAVAILABLE',
                    message=f'{name}/{versions[name]} 缺少历史数据，当前登记版本为 {meta.version}，不能替换冻结版本。',
                    suggested_action='恢复对应版本的计算定义，或重新创建使用当前版本的策略。')
            pending.append((name, window))
    if not pending:
        return
    sync_factor_meta.run(spark, context)
    for index, (name, (lower, upper)) in enumerate(pending):
        message = f'补齐研究因子 {name}（{index + 1}/{len(pending)}）：{lower} 至 {upper}'
        print('QUANTLENS_STRATEGY=' + message, flush=True)
        print('QUANTLENS_PROGRESS=' + json.dumps({'state': 'running', 'step': message}, ensure_ascii=False), flush=True)
        spark_factor_values.run(spark, context, names=[name], start=lower, end=upper)
