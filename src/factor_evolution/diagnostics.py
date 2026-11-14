"""Trusted, training-only evidence. No model-selected paths or SQL."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from exceptions import ContractViolation

COST_FIELDS = ['commission', 'transfer_fee', 'stamp_tax', 'other_fees', 'slippage_cost']


def fail(message):
    raise ContractViolation(code='RESEARCH_DIAGNOSTIC_INVALID', message=message, blocking=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, default=str).encode()).hexdigest()


def comparison_contract(config, protocol, profile):
    """Exclude the variable factor, include the entire frozen investment mandate."""
    from benchmarks.selection import plan_from_profile
    plan = plan_from_profile(profile)
    mandate = {k: v for k, v in profile.items() if k not in
               {'factors', 'standardization_chain', 'extra', 'display_name', 'source_chain'}}
    return {'capital': config.initial_capital, 'train_window': config.train_window.model_dump(),
            'protocol': protocol, 'portfolio': mandate,
            'benchmark_plan': plan.model_dump(mode='json')}


def summarize_training(nav, trades, positions, *, start, end, capital, benchmark):
    nav, trades, positions = [f.copy() for f in (nav, trades, positions)]
    if nav.empty:
        fail('训练诊断净值为空。')
    for frame in (nav, trades, positions):
        frame['date'] = pd.to_datetime(frame.date)
        if not frame.date.between(start, end).all():
            fail('诊断输入越出固定训练区间。')
    if nav.date.duplicated().any() or not nav.date.is_monotonic_increasing:
        fail('诊断净值日期必须唯一且递增。')
    if positions.duplicated(['date', 'instrument']).any():
        fail('持仓快照重复。')
    for frame in (trades, positions):
        if not frame.date.isin(nav.date).all():
            fail('持仓/交易存在净值未覆盖的日期。')
    columns = ['nav', 'cash', 'position_value', 'daily_return', 'benchmark_return', 'cost_paid']
    if not np.isfinite(nav[columns].to_numpy(dtype=float)).all() or (nav.nav <= 0).any():
        fail('净值或费用含无效值。')
    if not np.isfinite(trades[COST_FIELDS].to_numpy(dtype=float)).all() or (trades[COST_FIELDS] < 0).any(axis=None):
        fail('交易费用无效。')
    prev = nav.nav.shift(1, fill_value=capital)
    amounts = positions.groupby('date').market_value.sum().reindex(nav.date, fill_value=0).to_numpy()
    costs = trades.groupby('date')[COST_FIELDS].sum().sum(axis=1).reindex(nav.date, fill_value=0).to_numpy()
    checks = [np.allclose(nav.daily_return, nav.nav / prev - 1, atol=1e-10, rtol=1e-9),
              np.allclose(nav.nav, nav.cash + nav.position_value, atol=1e-6, rtol=1e-10),
              np.allclose(amounts, nav.position_value, atol=1e-6, rtol=1e-10),
              np.allclose(costs, nav.cost_paid, atol=1e-6, rtol=1e-10)]
    if not all(checks):
        fail('净值、现金、持仓或逐笔费用对账失败。')
    nav['pnl'] = nav.nav - prev
    nav['exposure'] = nav.position_value / nav.nav
    annual = []
    for year, rows in nav.groupby(nav.date.dt.year):
        annual.append({'year': int(year), 'days': len(rows),
                       'net_return': float((1 + rows.daily_return).prod() - 1),
                       'reference_return': float((1 + rows.benchmark_return).prod() - 1),
                       'net_pnl': float(rows.pnl.sum()), 'cost': float(rows.cost_paid.sum()),
                       'holdings_pnl_before_cost': float((rows.pnl + rows.cost_paid).sum()),
                       'mean_equity_exposure': float(rows.exposure.mean())})
    peaks = nav.nav.cummax().clip(lower=capital)
    dd = nav.nav / peaks - 1
    trough = int(dd.to_numpy().argmin())
    before = nav.nav.iloc[:trough + 1]
    peak = int(before.to_numpy().argmax()) if before.max() >= capital else -1
    episode = nav.iloc[peak + 1:trough + 1] if dd.iloc[trough] < 0 else nav.iloc[:0]
    filled = trades[trades.status == 'filled']
    return {
        'overview': {'start': str(nav.date.iloc[0].date()), 'end': str(nav.date.iloc[-1].date()),
                     'capital': capital, 'total_return': float(nav.nav.iloc[-1] / capital - 1),
                     'mean_equity_exposure': float(nav.exposure.mean()), 'benchmark': benchmark,
                     'reconciled': True},
        'annual': annual,
        'costs': {'total_cost': float(nav.cost_paid.sum()),
                  'components': {k: float(trades[k].sum()) for k in COST_FIELDS},
                  'filled_orders': len(filled), 'commission_at_five_orders': int(filled.commission.eq(5).sum()),
                  'median_filled_amount': float(filled.amount.median()) if len(filled) else None,
                  'status_counts': {str(k): int(v) for k, v in trades.status.value_counts().items()}},
        'drawdown': {'max_drawdown': float(dd.iloc[trough]),
                     'peak': str(nav.date.iloc[peak].date()) if peak >= 0 else 'initial_capital',
                     'trough': str(nav.date.iloc[trough].date()), 'days_to_trough': len(episode),
                     'net_pnl': float(episode.pnl.sum()), 'cost': float(episode.cost_paid.sum()),
                     'holdings_pnl_before_cost': float((episode.pnl + episode.cost_paid).sum())},
        'limitations': [
            '费用加回是实际成交路径的金额归因，不是无费用策略回测。',
            '平均仓位为日终仓位；本诊断不能将所有基准超额解释为纯选股贡献。',
            '同簇/同行业匹配基准随持仓变化；定义冻结不等于候选共享同一收益序列。',
            '订单状态记录可能跨日重复，不能当作独立失败交易。',
            '资金规模必须按实际订单重新回测，不能缩放已有结果。',
        ]}


def signal_summary(daily, *, start, end, horizons):
    if daily.empty:
        fail('没有成熟的训练期预测评价。')
    daily = daily.copy()
    daily['date'] = pd.to_datetime(daily.date)
    maturity = pd.to_datetime(daily.label_max_end)
    if (not daily.date.between(start, end).all()
            or not (maturity.gt(daily.date) & maturity.le(pd.Timestamp(end))).all()
            or daily.duplicated(['date', 'horizon']).any()
            or set(daily.horizon) != set(horizons)):
        fail('预测诊断日期、期限或唯一键不符合训练契约。')
    reports = []
    for horizon, group in daily.groupby('horizon'):
        ic = group.rank_ic.replace([np.inf, -np.inf], np.nan).dropna()
        if ic.empty:
            fail('训练期横截面不足或信号恒定，无法评价预测能力。')
        spread = (group.q5_ret - group.q1_ret).replace([np.inf, -np.inf], np.nan).dropna()
        if spread.empty or not group.coverage.between(0, 1).all():
            fail('预测诊断缺少有效分组收益或覆盖率。')
        reports.append({'horizon': int(horizon), 'days': len(group), 'valid_ic_days': len(ic),
                        'rank_ic_mean': float(ic.mean()), 'positive_ic_ratio': float((ic > 0).mean()),
                        'mean_coverage': float(group.coverage.mean()),
                        'top_minus_bottom': float(spread.mean()),
                        'annual_ic': {str(y): float(g.rank_ic.mean()) if g.rank_ic.notna().any() else None
                                      for y, g in group.groupby(group.date.dt.year)}})
    return {'horizons': reports, 'label': 'v1: signal t, entry t+1 close, exit t+1+h close',
            'scope': 'training_only',
            'limitations': '重叠期限的 IC 不独立；分组价差不是可实施多空收益，也不是扣费策略收益。'}


class EvidenceQueries:
    """An analyst can select sections, never dates, run IDs or file paths."""
    def __init__(self, evidence):
        self.evidence = evidence
        self.served = {}

    def query(self, section):
        if section not in {'overview', 'annual', 'costs', 'drawdown', 'signal', 'limitations'}:
            fail('未授权的训练证据查询。')
        packet = {'section': section, 'data': self.evidence['sections'][section],
                  'scope': 'training_only', 'run_id': self.evidence['run_id'],
                  'definition_hash': self.evidence['definition_hash']}
        packet['evidence_id'] = digest(packet)
        self.served[packet['evidence_id']] = packet
        return packet

    def verify(self, refs):
        if not refs:
            fail('研究假设没有证据引用。')
        for ref in refs:
            packet = self.served.get(ref.evidence_id)
            if packet is None:
                fail('研究假设引用了未查询或其他研究的证据。')
            value = packet['data']
            try:
                for key in ref.path:
                    value = value[key] if isinstance(value, dict) else value[int(key)]
            except (KeyError, IndexError, TypeError, ValueError):
                fail('研究证据指标路径无效。')
            if digest(value) != digest(ref.value):
                fail('研究假设篡改了所引用的证据数值。')
