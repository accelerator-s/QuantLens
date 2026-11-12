from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from exceptions import PortfolioContractError, QuantTrainingError
from market_rules import MarketRuleRegistry, RuleEngine
from paths import workspace

from .accounts import AccountRegistry
from .adapters import get_adapter
from .models import (
    Account,
    Action,
    EffectiveWindow,
    Plan,
    PlanState,
    Side,
    SuggestedTrade,
    TargetPosition,
)
from .store import PortfolioStore
from .trades import Positions, holdings_to_positions, refresh_available_qty

if TYPE_CHECKING:
    import pandas as pd

    from backtest.signal import RuntimeFactor
    from market_rules.sessions import TradingCalendar
    from strategy.models import ResolvedStrategyProfile

_LOOKBACK_BUFFER_DAYS = 180

_CLOSE_LOOKBACK_DAYS = 40

_MAX_SKIP_FACTOR = 5

# 结果结构

@dataclass(frozen=True)
class SkippedCandidate:

    instrument_id: str
    rank: int
    reason: str

@dataclass(frozen=True)
class BuildResult:

    plan: Plan
    skipped: list[SkippedCandidate]
    signal_date: str
    nav: Decimal
    cash_available: Decimal

@dataclass
class RecommendOutcome:

    plan: Plan
    plan_path: Path
    skipped: list[SkippedCandidate]
    signal_date: str
    nav: Decimal
    cash_available: Decimal
    staled_plan_ids: list[str] = field(default_factory=list)
    ai_ok: bool = True
    ai_error: str = ""

# 基础工具

def holdings_digest(df: pd.DataFrame) -> str:
    
    if df.empty:
        return "empty"
    lines = sorted(
        f"{row['instrument_id']}|{row['side']}|{row['qty']}|{row['cost_price']}"
        for _, row in df.iterrows()
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]

def _calendar_for(market: str) -> TradingCalendar:
    if market == "CN-A":
        from market_rules.markets.cn_a import ChinaAShareCalendar  

        return ChinaAShareCalendar()
    raise PortfolioContractError(
        code="portfolio.recommend_market_unsupported",
        message=f"市场 {market!r} 尚无交易日历接入，无法生成调仓建议",
        blocking=True,
        suggested_action="请先通过 framework issue 接入该市场的 TradingCalendar",
    )

def latest_close_row(*, as_of: str | None = None) -> tuple[str, pd.Series]:
    
    from panel import cache_status, load_panel  

    meta = cache_status()["default"].meta or {}
    params = meta.get("build_params") or {}
    cache_start, cache_end = params.get("start"), params.get("end")
    end = as_of or cache_end
    if end is None:
        raise PortfolioContractError(
            code="portfolio.recommend_panel_unavailable",
            message="矩阵缓存不存在或缺少构建参数，无法确定信号日",
            blocking=True,
            suggested_action="先执行 `data sync` 或 `data rebuild-cache` 构建面板缓存",
        )
    if cache_end is not None and end > cache_end:
        end = cache_end
    start = (date.fromisoformat(end) - timedelta(days=_CLOSE_LOOKBACK_DAYS)).isoformat()
    if cache_start is not None and start < cache_start:
        start = cache_start
    closes = load_panel(["close"], start=start, end=end)["close"]
    if closes.empty:
        raise PortfolioContractError(
            code="portfolio.recommend_panel_empty",
            message=f"面板 close 在 {start} → {end} 内没有数据",
            blocking=True,
            suggested_action="检查缓存覆盖范围（data cache-status）与 as_of 参数",
        )
    signal_date = closes.index.max().strftime("%Y-%m-%d")
    return signal_date, closes.ffill().iloc[-1]

def _price_of(close_row: pd.Series, instrument_id: str) -> Decimal | None:
    
    import math  

    value = close_row.get(instrument_id)
    if value is None:
        return None
    number = float(value)
    if math.isnan(number):
        return None
    return Decimal(str(value)).quantize(Decimal("0.0001"))

def _cash_bucket(account: Account):
    for bucket in account.cash:
        if bucket.currency == account.reporting_currency:
            return bucket
    raise PortfolioContractError(
        code="portfolio.recommend_cash_missing",
        message=(f"账户 {account.account_id} 无 {account.reporting_currency} 现金桶，无法生成建议"),
        context={"account_id": account.account_id, "currency": account.reporting_currency},
        blocking=True,
        suggested_action=(
            f"先 `account cash --id {account.account_id} "
            f"--currency {account.reporting_currency} --total <金额>` 设置现金"
        ),
    )

def investable_nav(
    account: Account,
    positions: Positions,
    close_row: pd.Series,
) -> Decimal:
    
    nav = _cash_bucket(account).available
    for (instrument_id, _side), p in positions.items():
        price = _price_of(close_row, instrument_id)
        if price is None:
            raise PortfolioContractError(
                code="portfolio.recommend_price_missing",
                message=f"持仓标的 {instrument_id} 无法取得现价（可能长期停牌）",
                context={"instrument_id": instrument_id},
                blocking=True,
                suggested_action=("该标的近期无收盘价；请人工处理该持仓（快照 / reinit）后重试"),
            )
        nav += p["qty"] * price
    return nav

def _validate_pair(
    account: Account, profile: ResolvedStrategyProfile, *, require_active_profile: bool
) -> None:
    from strategy.models import StrategyStatus  

    if require_active_profile and profile.status is not StrategyStatus.ACTIVE:
        raise PortfolioContractError(
            code="portfolio.recommend_profile_not_active",
            message=(
                f"策略 {profile.profile_name} 状态为 {profile.status.value}，"
                "仅 ACTIVE 策略可生成持仓建议"
            ),
            blocking=True,
            suggested_action="先 `configure strategy activate <名称> --confirm` 激活策略",
        )
    rule = MarketRuleRegistry().get(profile.trading_rules.market_rule)
    if account.market != profile.market or account.instrument_class != rule.instrument_class:
        raise PortfolioContractError(
            code="portfolio.recommend_scope_mismatch",
            message=(
                f"账户 {account.account_id}（{account.market}/{account.instrument_class}）"
                f"与策略 {profile.profile_name}（{profile.market}/{rule.instrument_class}）不匹配"
            ),
            blocking=True,
            suggested_action="请选择与策略市场 / 品种一致的账户",
        )

def _score_ranking(
    profile: ResolvedStrategyProfile,
    signal_date: str,
    *,
    runtime_factors: dict[str, RuntimeFactor] | None = None,
) -> list[str]:
    
    import numpy as np  

    from backtest.signal import compute_composite_scores  

    result = compute_composite_scores(
        profile,
        start=signal_date,
        end=signal_date,
        lookback_buffer_days=_LOOKBACK_BUFFER_DAYS,
        runtime_factors=runtime_factors,
    )
    valid = result.scores.iloc[-1].dropna()
    if valid.empty:
        raise PortfolioContractError(
            code="portfolio.recommend_no_scores",
            message=f"策略 {profile.profile_name} 在 {signal_date} 无任何有效得分",
            blocking=True,
            suggested_action="检查因子依赖字段与样本池配置（universe）",
        )
    order = np.lexsort((valid.index.to_numpy(), -valid.to_numpy(dtype="float64")))
    return [str(inst) for inst in valid.index.to_numpy()[order]]

# 计划构建（纯计算）

def build_plan(
    *,
    account: Account,
    profile: ResolvedStrategyProfile,
    as_of: str | None = None,
    runtime_factors: dict[str, RuntimeFactor] | None = None,
    require_active_profile: bool = True,
) -> BuildResult:
    
    from backtest.execution import _lot_round  
    from backtest.tradability import board_of  

    _validate_pair(account, profile, require_active_profile=require_active_profile)
    adapter = get_adapter(account.market, account.instrument_class)
    engine = RuleEngine(MarketRuleRegistry().resolve(account.market, account.instrument_class))
    calendar = _calendar_for(account.market)
    currency = account.reporting_currency

    signal_date, close_row = latest_close_row(as_of=as_of)
    signal_day = date.fromisoformat(signal_date)
    window_start = calendar.next_trading_day(signal_day)
    window_end = window_start
    for _ in range(3):
        window_end = calendar.next_trading_day(window_end)

    store = PortfolioStore()
    holdings_df = store.read_holdings(
        account.account_id, market=account.market, instrument_class=account.instrument_class
    )
    positions = holdings_to_positions(holdings_df)
    for (instrument_id, _side), p in positions.items():
        if p["currency"] != currency:
            raise PortfolioContractError(
                code="portfolio.recommend_currency_mixed",
                message=(
                    f"持仓 {instrument_id} 币种 {p['currency']} 与账户计价币种 {currency} 不一致"
                ),
                blocking=True,
                suggested_action="V1 建议引擎仅支持单币种账户；请核对持仓录入",
            )

    since = datetime.combine(window_start - timedelta(days=20), time.min)
    trades_df = store.read_trades(
        account.account_id,
        market=account.market,
        instrument_class=account.instrument_class,
        start=since,
    )
    refresh_available_qty(positions, trades_df, adapter=adapter, as_of=window_start)

    nav = investable_nav(account, positions, close_row)
    cash_available = _cash_bucket(account).available
    constraint = profile.position_constraint
    per_name_weight = min(
        (Decimal(1) - constraint.min_cash_ratio) / constraint.top_n,
        constraint.max_weight_per_name,
    )

    caveats: list[str] = []
    budget = cash_available
    frozen_map: dict[str, Decimal] = {}
    price_map: dict[str, Decimal] = {}
    for (instrument_id, _side), p in positions.items():
        price = _price_of(close_row, instrument_id)
        assert price is not None  
        price_map[instrument_id] = price
        frozen_map[instrument_id] = p["qty"] - p["available_qty"]
        if p["available_qty"] > 0:
            amount = p["available_qty"] * price
            budget += amount - engine.calculate_total_cost(amount, "sell").total
    reserve = constraint.min_cash_ratio * nav
    budget -= reserve
    if budget < 0:
        caveats.append(
            f"可用资金低于最低现金保留（min_cash_ratio={constraint.min_cash_ratio}），"
            "本期无法买入任何标的"
        )
        budget = Decimal("0")

    # 顺延替补选股
    ranking = _score_ranking(profile, signal_date, runtime_factors=runtime_factors)
    selected: dict[str, int] = {}
    skipped: list[SkippedCandidate] = []
    max_skips = constraint.top_n * _MAX_SKIP_FACTOR
    for rank, instrument_id in enumerate(ranking, start=1):
        if len(selected) >= constraint.top_n:
            break
        if len(skipped) >= max_skips:
            caveats.append(f"顺延替补已跳过 {len(skipped)} 名候选，停止继续替补（资金规模过小）")
            break
        price = _price_of(close_row, instrument_id)
        if price is None or price <= 0:
            skipped.append(SkippedCandidate(instrument_id, rank, "信号日无有效收盘价"))
            continue
        price_map[instrument_id] = price
        min_unit, increment = engine.resolve_min_unit(board_of(instrument_id))
        alloc = per_name_weight * nav
        shares = _lot_round(int(alloc / price), min_unit, increment)
        min_amount = (Decimal(min_unit) * price).quantize(Decimal("0.01"))
        if shares <= 0:
            reason = f"资金不足跳过：{instrument_id}（需≥{min_unit}股≈{min_amount}元）"
            skipped.append(SkippedCandidate(instrument_id, rank, reason))
            caveats.append(reason)
            continue
        frozen = frozen_map.get(instrument_id, Decimal("0"))
        buy_qty = Decimal(shares) - frozen
        cost = Decimal("0")
        if buy_qty > 0:
            amount = buy_qty * price
            cost = amount + engine.calculate_total_cost(amount, "buy").total
        if cost > budget:
            reason = f"资金不足跳过：{instrument_id}（需≥{min_unit}股≈{min_amount}元）"
            skipped.append(SkippedCandidate(instrument_id, rank, reason))
            caveats.append(reason)
            continue
        selected[instrument_id] = shares
        budget -= cost

    if not selected:
        caveats.append("本期未能选出任何可执行标的（资金或样本不足）")

    # 卖出与差额调整
    sells: list[SuggestedTrade] = []
    buys: list[SuggestedTrade] = []
    total_fees = Decimal("0")

    def _trade(instrument_id: str, action: Action, qty: Decimal, direction: str) -> SuggestedTrade:
        nonlocal total_fees
        price = price_map[instrument_id]
        amount = qty * price
        cost = engine.calculate_total_cost(amount, direction).total
        total_fees += cost
        return SuggestedTrade(
            market=account.market,
            instrument_class=account.instrument_class,
            instrument_id=instrument_id,
            side=Side.LONG,
            action=action,
            qty=qty,
            reference_price=price,
            currency=currency,
            estimated_cost=cost,
        )

    for (instrument_id, _side), p in sorted(positions.items()):
        qty, avail = p["qty"], p["available_qty"]
        if instrument_id not in selected:
            if avail <= 0:
                caveats.append(f"{instrument_id} 全部持仓处于 T+ 冻结，本期无法卖出")
            elif avail == qty:
                sells.append(_trade(instrument_id, Action.CLOSE, qty, "sell"))
            else:
                sells.append(_trade(instrument_id, Action.DECREASE, avail, "sell"))
                caveats.append(
                    f"{instrument_id} 有 {qty - avail} 股 T+ 冻结，本期仅能减仓 {avail} 股"
                )
            continue
        target = Decimal(selected[instrument_id])
        diff = target - qty
        if diff > 0:
            min_unit, increment = engine.resolve_min_unit(board_of(instrument_id))
            buy_qty = Decimal(_lot_round(int(diff), min_unit, increment))
            if buy_qty <= 0:
                caveats.append(f"{instrument_id} 目标差额 {diff} 股不足最小买入单位，保持现有持仓")
                selected[instrument_id] = int(qty)
                continue
            selected[instrument_id] = int(qty + buy_qty)
            buys.append(_trade(instrument_id, Action.INCREASE, buy_qty, "buy"))
        elif diff < 0:
            sell_needed = -diff
            sell_qty = min(sell_needed, avail)
            if sell_qty <= 0:
                caveats.append(f"{instrument_id} 冻结持仓高于目标，本期无法降至目标 {target} 股")
                selected[instrument_id] = int(qty)
                continue
            if sell_qty < sell_needed:
                caveats.append(
                    f"{instrument_id} 有 {qty - avail} 股 T+ 冻结，"
                    f"仅能减仓 {sell_qty} 股（目标差额 {sell_needed} 股）"
                )
            selected[instrument_id] = int(qty - sell_qty)
            sells.append(_trade(instrument_id, Action.DECREASE, sell_qty, "sell"))

    held = {inst for (inst, _side) in positions}
    for instrument_id, target in sorted(selected.items()):
        if instrument_id not in held and target > 0:
            buys.append(_trade(instrument_id, Action.OPEN, Decimal(target), "buy"))

    target_holdings = [
        TargetPosition(
            market=account.market,
            instrument_class=account.instrument_class,
            instrument_id=instrument_id,
            side=Side.LONG,
            qty=Decimal(target),
            weight=per_name_weight,
        )
        for instrument_id, target in sorted(selected.items())
        if target > 0
    ]

    manifest_date = _manifest_ref(account.market)
    plan = Plan(
        plan_id=f"PLN-{signal_day.strftime('%Y%m%d')}-{secrets.token_hex(3)}",
        account_ids=[account.account_id],
        generated_at=datetime.now(UTC),
        effective_window=EffectiveWindow(
            start=datetime.combine(window_start, time.min, tzinfo=UTC),
            end=datetime.combine(window_end, time.max, tzinfo=UTC),
        ),
        data_manifest_ref=manifest_date,
        strategy_profile_ref=profile.profile_name,
        market_rule_refs=[profile.trading_rules.market_rule],
        current_holdings_digest=holdings_digest(holdings_df),
        target_holdings=target_holdings,
        suggested_trades=sells + buys,
        trigger_reason=(
            f"规则引擎按策略 {profile.profile_name} 在 {signal_date} 的合成因子信号生成；"
            "AI 解读未运行"
        ),
        caveats=caveats,
        estimated_fees_by_currency={currency: str(total_fees)} if total_fees else {},
        state=PlanState.ACTIVE,
    )
    return BuildResult(
        plan=plan,
        skipped=skipped,
        signal_date=signal_date,
        nav=nav,
        cash_available=cash_available,
    )

def _manifest_ref(market: str) -> str:
    if market == "CN-A":
        from providers.markets.cn_a.csmar.incremental import get_last_date  

        last = get_last_date("prices_daily")
        return last.isoformat() if last else ""
    return ""

# 落盘 + AI 注解

def _latest_archived_plan(
    account: Account, profile_name: str, *, exclude_plan_id: str | None = None
) -> dict | None:
    archive_dir = (
        workspace().portfolio_plans(account.market, account.instrument_class, account.account_id)
        / "archive"
    )
    if not archive_dir.exists():
        return None
    latest: dict | None = None
    for path in archive_dir.glob("*/*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if raw.get("strategy_profile_ref") != profile_name:
            continue
        if exclude_plan_id is not None and raw.get("plan_id") == exclude_plan_id:
            continue
        if latest is None or str(raw.get("generated_at", "")) > str(latest.get("generated_at", "")):
            latest = raw
    return latest

def previous_plan_for(
    account: Account, profile_name: str, *, exclude_plan_id: str | None = None
) -> dict | None:
    
    latest: dict | None = None
    for _path, raw in PortfolioStore().list_active_plans(primary_account=account.account_id):
        if raw.get("strategy_profile_ref") != profile_name:
            continue
        if exclude_plan_id is not None and raw.get("plan_id") == exclude_plan_id:
            continue
        if latest is None or str(raw.get("generated_at", "")) > str(latest.get("generated_at", "")):
            latest = raw
    if latest is None:
        latest = _latest_archived_plan(account, profile_name, exclude_plan_id=exclude_plan_id)
    return latest

def generate_plan(
    *,
    account: Account,
    profile: ResolvedStrategyProfile,
    as_of: str | None = None,
    run_ai: bool = True,
    model_name: str | None = None,
    runtime_factors: dict[str, RuntimeFactor] | None = None,
    require_active_profile: bool = True,
) -> RecommendOutcome:
    
    store = PortfolioStore()
    build = build_plan(
        account=account,
        profile=profile,
        as_of=as_of,
        runtime_factors=runtime_factors,
        require_active_profile=require_active_profile,
    )

    staled: list[str] = []
    previous: dict | None = None
    for _path, raw in store.list_active_plans(primary_account=account.account_id):
        if raw.get("strategy_profile_ref") != profile.profile_name:
            continue
        if raw.get("state") != PlanState.ACTIVE.value:
            continue
        store.transition_plan(
            str(raw["plan_id"]),
            primary_account=account.account_id,
            new_state=PlanState.STALE_FOR_EXECUTION,
            confirm=True,
        )
        staled.append(str(raw["plan_id"]))
        if previous is None or str(raw.get("generated_at", "")) > str(
            previous.get("generated_at", "")
        ):
            previous = raw
    if previous is None:
        previous = _latest_archived_plan(account, profile.profile_name)

    plan_path = store.write_plan(build.plan, primary_account=account.account_id)
    outcome = RecommendOutcome(
        plan=build.plan,
        plan_path=plan_path,
        skipped=build.skipped,
        signal_date=build.signal_date,
        nav=build.nav,
        cash_available=build.cash_available,
        staled_plan_ids=staled,
    )
    if run_ai:
        from .plan_review import annotate_plan  

        try:
            annotate_plan(
                build.plan,
                account=account,
                previous_plan=previous,
                model_name=model_name,
            )
        except QuantTrainingError as exc:
            outcome.ai_ok = False
            outcome.ai_error = f"{exc.code}: {exc.message}"
    return outcome

def active_pairs() -> list[tuple[ResolvedStrategyProfile, Account]]:
    
    from strategy.models import StrategyStatus  
    from strategy.store import StrategyProfileStore  

    store = StrategyProfileStore()
    pairs: list[tuple[ResolvedStrategyProfile, Account]] = []
    for doc in store.list(status=StrategyStatus.ACTIVE):
        resolved = store.resolve(doc.profile_name)
        rule = MarketRuleRegistry().get(resolved.trading_rules.market_rule)
        for account in AccountRegistry().list(
            market=resolved.market, instrument_class=rule.instrument_class
        ):
            pairs.append((resolved, account))
    return pairs

def run_auto_recommend(console=None) -> list[dict]:  
    
    results: list[dict] = []
    for profile, account in active_pairs():
        entry: dict = {"profile": profile.profile_name, "account": account.account_id}
        try:
            outcome = generate_plan(account=account, profile=profile, run_ai=True)
        except QuantTrainingError as exc:
            entry.update(ok=False, error=exc.message, code=exc.code)
            if console is not None:
                console.print(
                    f"[red]✗[/red] {profile.profile_name} × {account.account_id}：{exc.message}"
                )
            results.append(entry)
            continue
        entry.update(ok=True, plan_id=outcome.plan.plan_id, ai_ok=outcome.ai_ok)
        if not outcome.ai_ok:
            entry["ai_error"] = outcome.ai_error
        if console is not None:
            mark = "[green]✓[/green]" if outcome.ai_ok else "[yellow]![/yellow]"
            suffix = "" if outcome.ai_ok else f"（AI 解读失败：{outcome.ai_error}）"
            console.print(
                f"{mark} {profile.profile_name} × {account.account_id} → "
                f"{outcome.plan.plan_id}{suffix}"
            )
        results.append(entry)
    return results

__all__ = [
    "BuildResult",
    "RecommendOutcome",
    "SkippedCandidate",
    "active_pairs",
    "build_plan",
    "generate_plan",
    "holdings_digest",
    "investable_nav",
    "latest_close_row",
    "previous_plan_for",
    "run_auto_recommend",
]
