from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import fields as dataclass_fields
from decimal import Decimal
from pathlib import Path

import pandas as pd
from rich.console import Console

from exceptions import BacktestError
from market_rules import MarketRuleRegistry, RuleEngine
from strategy import ResolvedStrategyProfile
from strategy.models import Frequency

from .accounting import Ledger
from .config import BacktestConfig
from .execution import Fill, Order, execute_day
from .metrics import compute_metrics
from .protocol import BacktestProtocol
from .report import BacktestRunStore
from .signal import RuntimeFactor
from .tradability import build_tradability

_PRICE_BUFFER_DAYS = 45

@dataclass
class BacktestOutcome:

    run_id: str
    run_dir: Path
    metrics: dict

# 辅助

def _signal_days(eval_index: pd.DatetimeIndex, frequency: Frequency) -> set[pd.Timestamp]:
    
    if frequency is Frequency.DAILY:
        days = set(eval_index)
    elif frequency in (Frequency.WEEKLY, Frequency.MONTHLY, Frequency.QUARTERLY):
        period = {Frequency.WEEKLY: "W", Frequency.MONTHLY: "M", Frequency.QUARTERLY: "Q"}[
            frequency
        ]
        ser = pd.Series(eval_index, index=eval_index)
        days = set(ser.groupby(eval_index.to_period(period)).max())
    else:
        raise BacktestError(
            code="BACKTEST_UNSUPPORTED_FREQUENCY",
            message=f"暂不支持的调仓频率：{frequency.value}（V1 支持 daily/weekly/monthly/quarterly）。",
            blocking=True,
            suggested_action="修改策略配置 rebalance_frequency。",
        )
    days.add(eval_index[0])
    return days

def _rebalance_orders(
    ledger: Ledger, targets: dict[str, Decimal], pending: dict[str, Order]
) -> tuple[list[Order], list[Order]]:
    
    for inst in targets:
        pending.pop(inst, None)
    sells: list[Order] = []
    buys: list[Order] = []
    nav = ledger.nav
    for inst in sorted(ledger.positions):
        if inst not in targets:
            sells.append(Order(instrument=inst, side="sell", sell_all=True, note="调出目标组合"))
    for inst, weight in sorted(targets.items()):
        delta = weight - ledger.weight_of(inst)
        amount = delta * nav
        if amount > 0:
            buys.append(Order(instrument=inst, side="buy", target_weight=weight, amount=amount))
        elif amount < 0 and inst in ledger.positions:
            sells.append(
                Order(
                    instrument=inst,
                    side="sell",
                    target_weight=weight,
                    amount=-amount,
                    note="减仓至目标权重",
                )
            )
    return sells, buys

def assess_objective(metrics, protocol):
    returns, risk, trading = metrics["returns"], metrics["risk"], metrics["trading"]
    drawdown = abs(risk["max_drawdown"])
    values = {"calmar":returns["annualized_return"]/drawdown if drawdown else 0.0,
              "sharpe":risk["sharpe"], "total_return":returns["total_return"]}
    constraints = protocol.objective.constraints
    eligible = drawdown <= constraints.max_drawdown and trading["annualized_turnover_oneside"] <= constraints.max_turnover
    return {"primary":protocol.objective.primary,"value":round(values[protocol.objective.primary],6),
            "eligible":eligible,"constraints":constraints.model_dump()}

# 主入口

def run_backtest(
    config: BacktestConfig,
    *,
    protocol: BacktestProtocol,
    profile: ResolvedStrategyProfile,
    console: Console | None = None,
    runtime_factors: dict[str, RuntimeFactor] | None = None,
    run_id: str | None = None,
    warehouse_inputs: dict | None = None,
) -> BacktestOutcome:
    
    console = console or Console()
    if runtime_factors:
        from quantlens.research_candidate import prepare_profile
        profile=prepare_profile(profile,runtime_factors)
        runtime_factors=None
    benchmark_plan = None
    benchmark_kind = protocol.benchmark
    if protocol.benchmark == "model_selected":
        from benchmarks.selection import freeze_profile, plan_from_profile
        profile = freeze_profile(profile, config.start, config.end)
        benchmark_plan = plan_from_profile(profile.model_dump(mode="json"))
        benchmark_kind = benchmark_plan.return_benchmark.kind
    import os
    if warehouse_inputs is None and os.environ.get('QUANTLENS_COMPUTE_LOCAL')!='1' and not os.path.isdir('/workspace'):
        from quantlens.remote_backtest import run_remote
        return run_remote(config,profile,protocol,run_id=run_id,reporter=console.print)
    if protocol.price_anchor != "next_close":
        raise BacktestError(
            code="BACKTEST_UNSUPPORTED_ANCHOR",
            message=f"撮合引擎仅支持 price_anchor=next_close，当前口径为 {protocol.price_anchor}。",
            blocking=True,
            suggested_action="请使用锁定协议的 next_close。",
        )

    rule_engine = RuleEngine(MarketRuleRegistry().get(protocol.market_rule_name))

    from quantlens.backtest_inputs import inputs
    from portfolio.construct import construct, Constraints
    payload = warehouse_inputs or inputs(profile, config.start, config.end)
    sig = payload["signal"]
    eval_index = pd.DatetimeIndex(sig.scores.index)
    if len(eval_index) == 0:
        raise BacktestError(code="BACKTEST_EMPTY_WINDOW",message="区间内无已发布信号",suggested_action="补齐 DWS 因子和权重")
    market = payload["market"]
    if benchmark_kind == 'same_cluster_equal_weight':
        from quantlens.research_preflight import require_style_panel

        require_style_panel(payload['clusters'], sig.scores)
    trad = build_tradability(market, rule_engine)
    close, ret_wd = market["close"], market["ret_wd"]
    bench_mask = payload["mask"].reindex(index=ret_wd.index,columns=ret_wd.columns).shift(1).fillna(False)
    trading = market['is_trading'].reindex_like(ret_wd).fillna(False)
    bad_return = (ret_wd.isin([float('inf'),float('-inf')]) | ret_wd.lt(-1)
                  | (ret_wd.isna() & trading)) & bench_mask
    if bad_return.any(axis=None):
        example = bad_return.loc[bad_return.any(axis=1)].iloc[:1].stack()
        raise BacktestError(code='BACKTEST_MARKET_RETURN_INVALID',
            message=f'历史股票池存在交易收益缺失或非法值：{list(example[example].index[:5])}；需修复行情接口。',
            blocking=True)
    # A quote gap represents an unchanged marked value, not removal of the
    # suspended member from the equal-weight benchmark denominator.
    benchmark_returns = ret_wd.where(trading | ret_wd.notna(),0.)
    universe_benchmark = benchmark_returns.where(bench_mask).mean(axis=1).reindex(eval_index).fillna(0.0)
    benchmark_metadata = {"name": benchmark_kind}
    if benchmark_plan:
        from benchmarks.returns import prepare
        bench_series, benchmark_metadata = prepare(benchmark_plan, payload, benchmark_returns, bench_mask, eval_index)
    else:
        if benchmark_kind not in {"same_cluster_equal_weight", "universe_equal_weight"}:
            raise BacktestError(code="BENCHMARK_PLAN_REQUIRED", message="此类基准需要回测前冻结的完整方案。")
        bench_series = pd.Series(0., index=eval_index) if benchmark_kind == 'same_cluster_equal_weight' else universe_benchmark.copy()
    benchmark_active_dates = []
    if benchmark_kind in {"same_cluster_equal_weight", "industry_equal_weight"}:
        source = payload["clusters"] if benchmark_kind == "same_cluster_equal_weight" else payload["industries"]
        clusters = source.reindex(ret_wd.index).shift(1).reindex(eval_index)

    observed_risk = market['is_delisting'].reindex(index=close.index,columns=close.columns).fillna(False)
    risk_signals = observed_risk.shift(1).fillna(False).reindex(eval_index)
    signal_days = _signal_days(eval_index, profile.rebalance_frequency)

    # -- run 目录 --
    store = BacktestRunStore.create(
        profile_name=profile.profile_name,
        run_id=run_id,
        config_payload={
            "config": config.model_dump(mode="json"),
            "config_hash": config.content_hash(),
            "protocol": protocol.model_dump(mode="json"),
            "protocol_hash": protocol.content_hash(),
            "protocol_locked": protocol.lock.locked,
            "profile": profile.model_dump(mode="json"),
            "factor_code_hashes": sig.factor_code_hashes,
            "factor_directions": sig.directions,
            "data_fingerprint": sig.provenance,
            "lineage": sig.provenance,
            "random_seed": 42,
            "research_standard": "cn_a_daily_v3",
            "market_rule": rule_engine.rule.model_dump(mode='json'),
            "signal_notes": sig.notes,
        },
    )
    console.print(f"[dim]run 目录：{store.run_dir}[/dim]")

    # -- 主循环 --
    ledger = Ledger(config.initial_capital)
    pending: dict[str, Order] = {}
    scheduled: dict[pd.Timestamp, dict[str, Decimal]] = {}
    trades: list[Fill] = []
    nav_rows: list[dict] = []
    position_rows: list[dict] = []
    prev_nav = float(config.initial_capital)

    try:
        for i, date in enumerate(eval_index):
            if benchmark_kind in {'same_cluster_equal_weight', 'industry_equal_weight'} and ledger.positions:
                # Benchmark matching uses the holdings before today's orders.
                # A liquidation later today must not hide a missing classification.
                benchmark_active_dates.append(str(date.date()))
                members=clusters.loc[date].where(bench_mask.loc[date]).dropna()
                held_clusters=clusters.loc[date].reindex(list(ledger.positions))
                if not members.empty and held_clusters.notna().all():
                    cluster_returns=benchmark_returns.loc[date].reindex(members.index).groupby(members).mean()
                    matched=cluster_returns.reindex(held_clusters.to_numpy())
                    if matched.notna().all():
                        bench_series.loc[date]=matched.mean()
                    else:
                        raise BacktestError(code='BACKTEST_BENCHMARK_MATCH_MISSING',
                            message=f'{date.date()} 持仓对应风格簇没有有效基准收益，需修复数据。', blocking=True)
                else:
                    missing = list(held_clusters.index[held_clusters.isna()])
                    raise BacktestError(code='BACKTEST_HOLDING_CLASSIFICATION_MISSING',
                        message=f'{date.date()} 实际持仓缺少历史分类或基准成员；证券：{missing[:10]}。',
                        blocking=True)
            from quantlens.security_master import CODE_CHANGES
            for event in CODE_CHANGES:
                if date.date() != event['effective']:
                    continue
                old,new=event['old'],event['new']
                if old in ledger.positions:
                    if new in ledger.positions:
                        raise BacktestError(code='SECURITY_IDENTITY_COLLISION',message=f'{old}/{new} 持仓身份重复。',blocking=True)
                    ledger.positions[new]=ledger.positions.pop(old)
                if old in pending:
                    order=pending.pop(old);order.instrument=new;pending[new]=order
                if date in scheduled and old in scheduled[date]:
                    scheduled[date][new]=scheduled[date].pop(old)
            held_returns = ret_wd.loc[date].reindex(list(ledger.positions))
            held_trading = trading.loc[date].reindex(held_returns.index)
            invalid = held_returns.isin([float('inf'),float('-inf')]) | held_returns.lt(-1) | (held_returns.isna() & held_trading)
            if invalid.any():
                raise BacktestError(code='BACKTEST_HOLDING_RETURN_INVALID',
                    message=f'{date.date()} 持仓收益数据非法：{list(invalid.index[invalid])}；需修复行情接口。', blocking=True)
            ledger.drift(ret_wd.loc[date])
            day_fills = []
            risk_exit = risk_signals.loc[date]
            current_risk = observed_risk.loc[date]
            targets = scheduled.pop(date, None)
            if targets is not None:
                targets = {inst:weight for inst,weight in targets.items() if not bool(risk_exit.get(inst,False))}
                sells, buys = _rebalance_orders(ledger, targets, pending)
            else:
                sells, buys = [], []
            sell_insts = {o.instrument for o in sells}
            for inst in sorted(ledger.positions):
                if bool(risk_exit.get(inst,False)) and inst not in sell_insts:
                    sells.append(Order(instrument=inst,side='sell',sell_all=True,note='此前已公开退市风险，下一收盘按正常限制退出'))
                    sell_insts.add(inst)
            for inst, order in sorted(pending.items()):
                if inst not in sell_insts:
                    sells.append(order)
            pending = {}

            result = execute_day(
                ledger,
                date=date,
                sells=sells,
                buys=buys,
                close_row=close.loc[date],
                trad=trad,
                rule_engine=rule_engine,
                protocol=protocol,
                min_cash_ratio=Decimal(str(profile.constraints.cash_reserve)),
            )
            pending = dict(result.pending_sells)
            day_fills.extend(result.fills)
            trades.extend(day_fills)

            if date in signal_days and i + 1 < len(eval_index):
                candidates = sig.scores.loc[date].rename("score").rename_axis("code").reset_index()
                candidates["cluster_id"] = candidates.code.map(payload["clusters"].loc[date])
                candidates["industry_code"] = candidates.code.map(payload["industries"].loc[date])
                candidates["in_universe"] = candidates.code.map(payload["mask"].loc[date]) & ~candidates.code.map(current_risk).fillna(False)
                candidates['close'] = candidates.code.map(close.loc[date])
                weights = construct(candidates, Constraints(**profile.constraints.model_dump()),
                                    breadth=payload["breadth"].get(date), regime=profile.risk.regime_filter.model_dump(),
                                    star_market_enabled=config.star_market_enabled,
                                    capital=float(ledger.nav) if profile.extra.get('account_binding') else None)
                scheduled[eval_index[i + 1]] = dict(zip(weights.code, map(lambda x:Decimal(str(x)),weights.weight)))

            nav_now = float(ledger.nav)
            bench_r = float(bench_series.loc[date])
            daily_r = nav_now / prev_nav - 1.0
            nav_rows.append(
                {
                    "date": date,
                    "nav": nav_now,
                    "cash": float(ledger.cash),
                    "position_value": float(ledger.position_value),
                    "daily_return": daily_r,
                    "benchmark_return": bench_r,
                    "excess_return": daily_r - bench_r,
                    "turnover_oneside": float(result.buy_amount) / nav_now if nav_now > 0 else 0.0,
                    "buy_amount": float(result.buy_amount),
                    "sell_amount": float(result.sell_amount),
                    "cost_paid": float(result.cost_paid),
                    "n_holdings": len(ledger.positions),
                }
            )
            prev_nav = nav_now
            for snap in ledger.snapshot_positions():
                position_rows.append({"date": date, **snap})
        # -- 落盘与指标 --
        fill_columns = [f.name for f in dataclass_fields(Fill)]
        trades_frame = pd.DataFrame([asdict(f) for f in trades], columns=fill_columns)
        nav_frame = pd.DataFrame(nav_rows)
        positions_frame = pd.DataFrame(
            position_rows, columns=["date", "instrument", "market_value", "shares_nominal", "weight"]
        )
        store.write_daily("trades", trades_frame)
        store.write_daily("positions", positions_frame)
        store.write_daily("nav", nav_frame)

        metrics = compute_metrics(
            nav_frame,
            trades_frame,
            initial_capital=float(config.initial_capital),
            trading_days_per_year=protocol.trading_days_per_year,
        )
        metrics['research_standard'] = 'cn_a_daily_v3'
        metrics['benchmark'] = {**benchmark_metadata,
            'active_days':len(benchmark_active_dates),
            'empty_portfolio_policy':'cash_zero_return' if benchmark_kind in {'same_cluster_equal_weight','industry_equal_weight'} else 'benchmark_continues',
            'universe_reference_total_return':float((1.+universe_benchmark).prod()-1.)}
        metrics['input_quality'] = {'passed':True,'issues':[]}
        metrics["objective"] = assess_objective(metrics, protocol)
        store.write_metrics(metrics)

        chart = nav_frame.set_index("date")[["nav"]].copy()
        chart["benchmark_nav"] = (
            float(config.initial_capital)
            * (1.0 + nav_frame.set_index("date")["benchmark_return"]).cumprod()
        )
        store.write_chart("nav", chart)
        store.update_status(
            state="completed",
            extra={"trading_days": len(eval_index), "final_nav": round(prev_nav, 2)},
        )
        return BacktestOutcome(run_id=store.run_id, run_dir=store.run_dir, metrics=metrics)
    except Exception:
        store.delete()
        raise

__all__ = ["BacktestOutcome", "run_backtest"]
