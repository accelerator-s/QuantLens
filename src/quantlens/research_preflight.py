"""Research data gates shared by the warehouse and cached backtests."""
from __future__ import annotations

import pandas as pd

from exceptions import ContractViolation


def assess_input_quality(metrics: dict) -> dict:
    quality = metrics.get('input_quality')
    if metrics.get('research_standard') != 'cn_a_daily_v3' or not quality:
        return {'passed':False,'issues':['缺少当前统一规则的输入验收证据，必须重新计算。']}
    return {'passed':bool(quality['passed']),'issues':list(quality.get('issues',[]))}


def require_style_dates(coverage: pd.Series) -> None:
    if coverage.empty:
        raise ContractViolation(code='RESEARCH_STYLE_COVERAGE_MISSING',
            message='研究区间没有可检查的股票池日期或风格簇表尚未建立。', blocking=True,
            suggested_action='补齐研究区间的股票池和样本外风格簇，再重新检查。')
    missing = coverage.isna() | coverage.le(0)
    if missing.any():
        examples = [str(value)[:10] for value in coverage.index[missing][:10]]
        raise ContractViolation(code='RESEARCH_STYLE_COVERAGE_MISSING',
            message=f'研究区间缺少样本外风格簇，缺失 {int(missing.sum())} 个交易日；示例：{examples}。',
            context={'missing_days': int(missing.sum()), 'sample_dates': examples},
            blocking=True, suggested_action=(
                '先核对历史行情覆盖，再用过去的训练窗口生成完整股票池的样本外风格簇；'
                '通过检查后重跑。禁止把全体股票归入未知簇后继续验收因子。'))


def require_style_panel(clusters: pd.DataFrame, scores: pd.DataFrame) -> None:
    aligned = clusters.reindex(index=scores.index, columns=scores.columns)
    eligible = scores.notna()
    coverage = (aligned.notna() & eligible).sum(axis=1)
    require_style_dates(coverage)
    missing = eligible & aligned.isna()
    if missing.any(axis=None):
        examples = missing.loc[missing.any(axis=1)].iloc[:10].stack()
        examples = [str(key) for key in examples[examples].index[:10]]
        raise ContractViolation(code='RESEARCH_STYLE_STOCKS_MISSING',
            message=f'实际候选仍有 {int(missing.sum().sum())} 个日期/证券缺少风格分类；示例：{examples}。',
            context={'missing_cells': int(missing.sum().sum()), 'examples': examples},
            blocking=True, suggested_action='补齐这些证券在对应历史日期的分类，禁止跳过或统一塞入未知簇后继续研究。')


def check_warehouse_style(spark, start: str, end: str) -> dict:
    from pyspark.sql import functions as F

    if not spark.catalog.tableExists('dws.style_cluster'):
        require_style_dates(pd.Series(dtype=float))
    universe = (spark.table('dwd.universe')
        .where(F.col('date').between(start, end) & F.col('in_universe'))
        .select('code', 'date'))
    clusters = (spark.table('dws.style_cluster')
        .where(F.col('date').between(start, end)))
    if 'version' not in clusters.columns:
        raise ContractViolation(code='RESEARCH_STYLE_METHOD_MISSING',
            message='风格分类缺少方法版本，无法核对研究口径。', blocking=True)
    methods = [row.version for row in clusters.select('version').distinct().limit(2).collect()]
    if len(methods) > 1 or (methods and not methods[0]):
        raise ContractViolation(code='RESEARCH_STYLE_METHOD_MIXED',
            message=f'研究区间混用了风格分类方法或缺少方法版本：{methods}。', blocking=True,
            suggested_action='用同一套已登记规则重建完整研究区间，再重跑基线和固定候选。')
    if clusters.groupBy('code', 'date').count().where('count>1').limit(1).count():
        raise ContractViolation(code='RESEARCH_STYLE_DUPLICATE',
            message='风格分类存在重复日期/证券键。', blocking=True)
    invalid = clusters.where(F.col('train_end').isNull()
                            | (F.col('train_end') >= F.col('date'))).limit(1).collect()
    if invalid:
        raise ContractViolation(code='RESEARCH_STYLE_LOOKAHEAD',
            message='风格簇没有训练截止日，或训练数据达到预测日。', blocking=True,
            suggested_action='按严格过去窗口重新训练风格簇，再开始因子研究。')
    dates = universe.select('date').distinct()
    daily = (universe.join(clusters.select('code', 'date', 'cluster_id'), ['code', 'date'], 'left')
        .groupBy('date').agg(F.count('cluster_id').alias('covered'), F.count('*').alias('population')))
    rows = dates.join(daily, 'date', 'left').orderBy('date').collect()
    coverage = pd.Series([row.covered for row in rows],
                         index=pd.to_datetime([row.date for row in rows]), dtype=float)
    require_style_dates(coverage)
    gaps = [row for row in rows if row.covered != row.population]
    if gaps:
        raise ContractViolation(code='RESEARCH_STYLE_STOCKS_MISSING',
            message=f'股票池在 {len(gaps)} 个交易日没有完整风格分类；首日 {gaps[0].date}，'
                    f'覆盖 {gaps[0].covered}/{gaps[0].population}。', blocking=True,
            suggested_action='按覆盖完整股票池的独立风格特征重新计算分类；不要使用选股因子的盈利或缺失筛选。')
    return {'passed': True, 'start': start, 'end': end, 'dates': len(rows),
            'method_version': methods[0],
            'min_daily_covered': int(coverage.min()),
            'min_daily_coverage': min(row.covered / row.population for row in rows)}
