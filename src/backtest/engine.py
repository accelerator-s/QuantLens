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
from .signal import RuntimeFactor, compute_composite_scores, target_weights
from .tradability import build_tradability

_MARKET_FIELDS = ["close", "high", "low", "is_trading", "listed_days", "trading_status", "ret_wd"]
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

def _data_fingerprint(field_names: list[str]) -> dict:
    
    from panel import cache_status

    meta = cache_status()["default"].meta or {}
    axes = meta.get("axes") or {}
    fields_meta = meta.get("fields") or {}
    return {
        "axes": {
            "dates_hash": axes.get("dates_hash"),
            "instruments_hash": axes.get("instruments_hash"),
        },
        "fields": {
            name: (fields_meta.get(name) or {}).get("data_hash")
            for name in sorted(set(field_names))
        },
    }

def _factor_dependencies(profile: ResolvedStrategyProfile) -> list[str]:
    from factors.registry import FactorRegistry

    registry = FactorRegistry()
    deps: set[str] = set()
    for ref in profile.factors.items:
        deps.update(registry.get(ref.name).dependencies)
    return sorted(deps)

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

# 主入口

def run_backtest(
    config: BacktestConfig,
    *,
    protocol: BacktestProtocol,
    profile: ResolvedStrategyProfile,
    console: Console | None = None,
    runtime_factors: dict[str, RuntimeFactor] | None = None,
    run_id: str | None = None,
) -> BacktestOutcome:
    
    console = console or Console()
    if protocol.price_anchor != "next_close":
        raise BacktestError(
            code="BACKTEST_UNSUPPORTED_ANCHOR",
            message=f"V1 引擎仅支持 price_anchor=next_close，当前口径为 {protocol.price_anchor}。",
            blocking=True,
            suggested_action="V1 请使用 next_close；next_open 属后续扩展。",
        )

    rule_engine = RuleEngine(MarketRuleRegistry().get(protocol.market_rule_name))

    # -- 信号 --
    console.print("[dim]信号：本地执行因子 → 标准化链 → 合成得分…[/dim]")
    sig = compute_composite_scores(
        profile,
        start=config.start,
        end=config.end,
        lookback_buffer_days=config.lookback_buffer_days,
        runtime_factors=runtime_factors,
    )
    eval_index = pd.DatetimeIndex(sig.scores.index)

    console.print("[dim]行情：加载执行面板与可交易性掩码…[/dim]")
    from panel import load_panel
    from panel.universe import build_universe_mask

    from .signal import _cache_bounds, _clamp  # 复用缓存收边逻辑

    cache_lo, cache_hi = _cache_bounds()
    load_start = _clamp(
        (pd.Timestamp(config.start) - pd.Timedelta(days=_PRICE_BUFFER_DAYS)).strftime("%Y-%m-%d"),
        cache_lo,
        cache_hi,
    )
    load_end = _clamp(config.end, cache_lo, cache_hi)
    market = load_panel(_MARKET_FIELDS, start=load_start, end=load_end)
    is_trading_full = load_panel(["is_trading"])["is_trading"]
    trad = build_tradability(market, rule_engine, is_trading_full=is_trading_full)

    close = market["close"]
    ret_wd = market["ret_wd"]

    bench_mask = build_universe_mask(
        profile.universe.to_panel_config(),
        is_trading=market["is_trading"],
        listed_days=market["listed_days"],
        trading_status=market["trading_status"],
    )
    bench_series = ret_wd.where(bench_mask).mean(axis=1).reindex(eval_index).fillna(0.0)

    signal_days = _signal_days(eval_index, profile.rebalance_frequency)

    # -- run 目录 --
    runtime_dependencies = [
        dep for factor in (runtime_factors or {}).values() for dep in factor.dependencies
    ]
    used_fields = _MARKET_FIELDS + sorted(
        set(runtime_dependencies or _factor_dependencies(profile))
    )
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
            "data_fingerprint": _data_fingerprint(used_fields),
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
                min_cash_ratio=profile.position_constraint.min_cash_ratio,
            )
            pending = dict(result.pending_sells)
            day_fills.extend(result.fills)
            trades.extend(day_fills)

            if date in signal_days and i + 1 < len(eval_index):
                scheduled[eval_index[i + 1]] = target_weights(
                    sig.scores.loc[date], profile.position_constraint
                )

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
