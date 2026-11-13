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
from .execution import Fill, Order, execute_day, force_close
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

def _delist_disposal(
    ledger: Ledger,
    *,
    date: pd.Timestamp,
    close: pd.DataFrame,
    last_trading_date: pd.Series,
    pending: dict[str, Order],
    rule_engine: RuleEngine,
    protocol: BacktestProtocol,
) -> list[Fill]:
    
    fills: list[Fill] = []
    for inst in sorted(ledger.positions):
        ltd = last_trading_date.get(inst)
        if ltd is None or pd.isna(ltd):
            continue
        if date == ltd and protocol.delist_force_close:
            px = close.at[date, inst] if inst in close.columns else None
            if px is not None and pd.notna(px):
                fills.append(
                    force_close(
                        ledger,
                        inst,
                        date=date,
                        close=float(px),
                        rule_engine=rule_engine,
                        protocol=protocol,
                    )
                )
            else:  
                written = ledger.writeoff(inst, recovery_rate=protocol.delist_recovery_rate)
                fills.append(
                    Fill(
                        date=date,
                        instrument=inst,
                        side="sell",
                        status="writeoff_delisted",
                        amount=float(written * protocol.delist_recovery_rate),
                        note=f"末日无收盘价，按回收率 {protocol.delist_recovery_rate} 核销",
                    )
                )
            pending.pop(inst, None)
        elif date > ltd:
            written = ledger.writeoff(inst, recovery_rate=protocol.delist_recovery_rate)
            fills.append(
                Fill(
                    date=date,
                    instrument=inst,
                    side="sell",
                    status="writeoff_delisted",
                    amount=float(written * protocol.delist_recovery_rate),
                    note=(
                        f"退市核销：最后可交易日 {pd.Timestamp(ltd).date()}，"
                        f"核销市值 {float(written):.2f}，"
                        f"回收率 {protocol.delist_recovery_rate}"
                    ),
                )
            )
            pending.pop(inst, None)
    return fills

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
    import os
    if warehouse_inputs is None and os.environ.get('QUANTLENS_COMPUTE_LOCAL')!='1' and not os.path.isdir('/workspace'):
        from quantlens.remote_backtest import run_remote
        return run_remote(config,profile,protocol,run_id=run_id)
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
    trad = build_tradability(market, rule_engine, delisting_dates=payload["delisting_dates"])
    close, ret_wd = market["close"], market["ret_wd"]
    bench_mask = payload["mask"].reindex(index=ret_wd.index,columns=ret_wd.columns).shift(1).fillna(False)
    bench_series = ret_wd.where(bench_mask).mean(axis=1).reindex(eval_index).fillna(0.0)
    benchmark_fallback_dates = []
    if protocol.benchmark == "same_cluster_equal_weight":
        clusters = payload["clusters"].reindex(ret_wd.index).shift(1).reindex(eval_index)
        for dt in eval_index:
            members = clusters.loc[dt].where(bench_mask.loc[dt]).dropna()
            if members.empty:
                benchmark_fallback_dates.append(str(dt.date()))
                continue

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
            if protocol.benchmark == 'same_cluster_equal_weight' and ledger.positions:
                members=clusters.loc[date].where(bench_mask.loc[date]).dropna()
                held_clusters=clusters.loc[date].reindex(list(ledger.positions))
                if not members.empty and held_clusters.notna().all():
                    cluster_returns=ret_wd.loc[date].reindex(members.index).groupby(members).mean()
                    matched=cluster_returns.reindex(held_clusters.to_numpy())
                    if matched.notna().all():bench_series.loc[date]=matched.mean()
                    elif str(date.date()) not in benchmark_fallback_dates:benchmark_fallback_dates.append(str(date.date()))
                elif str(date.date()) not in benchmark_fallback_dates:benchmark_fallback_dates.append(str(date.date()))
            ledger.drift(ret_wd.loc[date])
            day_fills = _delist_disposal(
                ledger,
                date=date,
                close=close,
                last_trading_date=trad.last_trading_date,
                pending=pending,
                rule_engine=rule_engine,
                protocol=protocol,
            )

            targets = scheduled.pop(date, None)
            if targets is not None:
                sells, buys = _rebalance_orders(ledger, targets, pending)
            else:
                sells, buys = [], []
            sell_insts = {o.instrument for o in sells}
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
                candidates["in_universe"] = candidates.code.map(payload["mask"].loc[date])
                weights = construct(candidates, Constraints(**profile.constraints.model_dump()),
                                    breadth=payload["breadth"].get(date), regime=profile.risk.regime_filter.model_dump(),
                                    star_market_enabled=config.star_market_enabled)
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
    except Exception:
        store.update_status(state="failed")
        raise

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
    metrics["benchmark"] = {"name":protocol.benchmark,"fallback_used":bool(benchmark_fallback_dates),"fallback_dates":benchmark_fallback_dates}
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

__all__ = ["BacktestOutcome", "run_backtest"]
