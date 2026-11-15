"""Reconciled currency P&L attribution on the realized execution path."""
import numpy as np
import pandas as pd


def attribute(nav, trades, positions, *, capital, episode_dates):
    from .diagnostics import COST_FIELDS, fail
    if not {'instrument', 'side'}.issubset(trades.columns):
        return {'available': False, 'reason': '旧成交记录缺少证券或买卖方向'}
    dates = pd.DatetimeIndex(nav.date)
    filled = trades.loc[trades.status.eq('filled')].copy()
    codes = sorted(set(positions.instrument) | set(filled.instrument))
    if not codes:
        return {'available': True, 'reconciled': True, 'stocks': [], 'industries': [], 'market_states': []}
    values = positions.pivot(index='date', columns='instrument', values='market_value').reindex(
        index=dates, columns=codes).fillna(0.)
    filled['gross_flow'] = np.where(filled.side.eq('buy'),
        filled.amount-filled.slippage_cost, -(filled.amount+filled.slippage_cost))
    filled['cost'] = filled[COST_FIELDS].sum(axis=1)
    def matrix(field):
        return filled.groupby(['date', 'instrument'])[field].sum().unstack('instrument').reindex(
            index=dates, columns=codes).fillna(0.)
    costs = matrix('cost')
    gross = values.diff().fillna(values)-matrix('gross_flow')
    net = gross-costs
    expected = nav.nav.to_numpy()-np.r_[capital, nav.nav.to_numpy()[:-1]]
    residual = net.sum(axis=1).to_numpy()-expected
    if not np.allclose(residual, 0, atol=1e-5, rtol=0):
        fail('股票损益与净值变化无法对账。')
    stock = pd.DataFrame({'instrument': codes, 'net_pnl': net.sum().to_numpy(),
        'gross_pnl': gross.sum().to_numpy(), 'cost': costs.sum().to_numpy(),
        'drawdown_pnl': net.reindex(episode_dates).sum().to_numpy()}).sort_values('net_pnl')
    industries = []
    if 'industry_code' in positions:
        groups = positions.pivot(index='date', columns='instrument', values='industry_code').reindex(
            index=dates, columns=codes).ffill().fillna('unknown')
        # Prior holding classification covers full exits. Current classification
        # covers buys. No future classification is backfilled into earlier dates.
        long = pd.DataFrame({'industry': groups.to_numpy().ravel(),
            'net_pnl': net.to_numpy().ravel(), 'cost': costs.to_numpy().ravel()})
        industries = long.groupby('industry')[['net_pnl', 'cost']].sum().reset_index().to_dict('records')
    state = np.where(nav.benchmark_return > 0, 'benchmark_up',
                     np.where(nav.benchmark_return < 0, 'benchmark_down', 'benchmark_flat'))
    market = pd.DataFrame({'state': state, 'net_pnl': expected, 'cost': nav.cost_paid.to_numpy()})
    return {'available': True, 'reconciled': True, 'max_daily_residual': float(abs(residual).max()),
        'net_pnl': float(net.to_numpy().sum()), 'stocks': stock.to_dict('records'),
        'worst_stocks': stock.head(10).to_dict('records'), 'best_stocks': stock.tail(10).to_dict('records'),
        'industries': industries, 'market_states': market.groupby('state').agg(
            net_pnl=('net_pnl', 'sum'), cost=('cost', 'sum'), days=('net_pnl', 'size')).reset_index().to_dict('records'),
        'scope': 'realized_currency_pnl_not_causal_alpha',
        'limitations': '市场状态按当日基准涨跌作事后描述，不是可交易择时信号；证券代码迁移可能影响个股分项。'}
