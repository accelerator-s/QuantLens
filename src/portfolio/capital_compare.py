from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from exceptions import PortfolioContractError, QuantTrainingError
from paths import workspace

from .models import Account
from .recommend import investable_nav, latest_close_row
from .store import PortfolioStore
from .trades import holdings_to_positions

if TYPE_CHECKING:
    from rich.console import Console

_TASK = "structured_config"
_MAX_ATTEMPTS = 3

_WINDOW_DAYS = 365

_DELTA_KEYS: tuple[tuple[str, str], ...] = (
    ("returns", "total_return"),
    ("returns", "annualized_return"),
    ("returns", "annualized_excess_return"),
    ("risk", "sharpe"),
    ("risk", "max_drawdown"),
    ("risk", "annualized_volatility"),
    ("trading", "annualized_turnover_oneside"),
    ("trading", "annualized_cost_drag"),
    ("trading", "total_cost_paid"),
    ("trading", "avg_holdings"),
    ("orders", "lot_unaffordable_count"),
    ("orders", "lot_unaffordable_amount_ratio"),
)

_SYSTEM = (
    "你是量化持仓决策的资金口径分析助手。两份回测在同一策略、同一历史窗口下运行，"
    "唯一差异是初始资金（参考资金 vs 账户实际资金）。请输出 JSON 格式的 "
    "structured_config：\n"
    "- config_kind: 固定为 'capital_comparison'\n"
    "- proposed_value: dict，必须包含：\n"
    "  - summary: str，对比结论（3-5 句），必须明确说明这是**历史模拟执行落差**"
    "对比、不构成未来收益预测\n"
    "  - shortfall_causes: list[str]，1-5 条落差成因分析（如整手买不起、"
    "费用占比、持仓集中度变化等）\n"
    "- open_questions: list[str]，需要持有人线下确认的问题（可为空）\n\n"
    "硬性要求：输出必须是纯 JSON 对象；不得在 JSON 顶层输出规定之外的字段，"
    "summary 与 shortfall_causes 必须位于 proposed_value 内；"
    "只依据给出的指标，不得编造数据或做收益预测。"
)

def _comparison_path(account: Account, plan_id: str) -> Path:
    return (
        workspace().portfolio_plans(account.market, account.instrument_class, account.account_id)
        / "comparisons"
        / f"{plan_id}.yaml"
    )

def _window() -> tuple[str, str]:
    
    from panel import cache_status  

    meta = cache_status()["default"].meta or {}
    params = meta.get("build_params") or {}
    cache_start, cache_end = params.get("start"), params.get("end")
    if not cache_end:
        raise PortfolioContractError(
            code="portfolio.comparison_window_unavailable",
            message="矩阵缓存不存在或缺少构建参数，无法确定对比窗口",
            blocking=True,
            suggested_action="先执行 `data sync` 或 `data rebuild-cache` 构建面板缓存",
        )
    start = (date.fromisoformat(cache_end) - timedelta(days=_WINDOW_DAYS)).isoformat()
    if cache_start and start < cache_start:
        start = cache_start
    return start, cache_end

def _account_capital(account: Account) -> Decimal:
    _signal_date, close_row = latest_close_row()
    holdings = PortfolioStore().read_holdings(
        account.account_id, market=account.market, instrument_class=account.instrument_class
    )
    capital = investable_nav(account, holdings_to_positions(holdings), close_row)
    if capital <= 0:
        raise PortfolioContractError(
            code="portfolio.comparison_zero_capital",
            message=f"账户 {account.account_id} 可投资净值为 0，无法做资金口径对比",
            blocking=True,
            suggested_action=(
                f"先 `account cash --id {account.account_id} "
                f"--currency {account.reporting_currency} --total <金额>` 设置现金"
            ),
        )
    return capital

def _run_once(
    profile_name: str,
    *,
    start: str,
    end: str,
    capital: Decimal,
    console: Console | None,
):
    from backtest.config import BacktestConfig, resolve_and_check  
    from backtest.engine import run_backtest  
    from backtest.protocol import verify_protocol_or_raise  

    config = BacktestConfig(
        profile_name=profile_name,
        start=start,
        end=end,
        initial_capital=capital,
    )
    protocol = verify_protocol_or_raise()
    resolved = resolve_and_check(config, protocol)
    
    return run_backtest(config, protocol=protocol, profile=resolved, console=console)

def _metric_deltas(reference: dict, account: dict) -> dict[str, float | None]:
    deltas: dict[str, float | None] = {}
    for group, key in _DELTA_KEYS:
        ref_val = (reference.get(group) or {}).get(key)
        acc_val = (account.get(group) or {}).get(key)
        label = f"{group}.{key}"
        if isinstance(ref_val, int | float) and isinstance(acc_val, int | float):
            deltas[label] = round(float(acc_val) - float(ref_val), 6)
        else:
            deltas[label] = None
    return deltas

def _ai_summary(
    *,
    plan_id: str,
    profile_name: str,
    window: tuple[str, str],
    reference_capital: Decimal,
    account_capital: Decimal,
    reference_metrics: dict,
    account_metrics: dict,
    deltas: dict,
    model_name: str | None,
) -> dict:
    
    from ai.router import AIRouter  

    prompt = "\n".join(
        [
            f"计划：{plan_id}；策略：{profile_name}；窗口：{window[0]} → {window[1]}",
            f"参考资金：{reference_capital} 元；账户实际资金：{account_capital} 元",
            "\n参考资金回测指标：",
            json.dumps(reference_metrics, ensure_ascii=False, indent=2),
            "\n账户资金回测指标：",
            json.dumps(account_metrics, ensure_ascii=False, indent=2),
            "\n指标差值（账户 − 参考）：",
            json.dumps(deltas, ensure_ascii=False, indent=2),
            "\n请分析资金规模差异带来的历史执行落差成因，按 system 中的 "
            "structured_config 格式输出。",
        ]
    )
    router = AIRouter()
    last_detail = ""
    for attempt in range(_MAX_ATTEMPTS):
        result = router.run_task(
            task=_TASK,
            prompt=prompt
            if attempt == 0
            else f"{prompt}\n\n注意：你上一次输出校验失败：{last_detail}\n请修正后重新输出。",
            system=_SYSTEM,
            model_name=model_name,
        )
        value = result.get("proposed_value") or {}
        summary = value.get("summary")
        causes = value.get("shortfall_causes")
        if not isinstance(summary, str) or not summary.strip():
            last_detail = "proposed_value.summary 必须是非空字符串。"
            continue
        if (
            not isinstance(causes, list)
            or not causes
            or not all(isinstance(c, str) and c.strip() for c in causes)
        ):
            last_detail = "proposed_value.shortfall_causes 必须是非空的字符串列表。"
            continue
        return {
            "ok": True,
            "model": result.get("model_name"),
            "summary": summary.strip(),
            "shortfall_causes": [c.strip() for c in causes],
            "error": "",
        }
    raise PortfolioContractError(
        code="portfolio.comparison_ai_invalid",
        message=f"资金对比 AI 解读连续 {_MAX_ATTEMPTS} 次未通过本地校验：{last_detail}",
        blocking=False,
        suggested_action="稍后重跑资金对比，或加 --model 指定注册表内模型名",
    )

def run_capital_comparison(
    *,
    plan_id: str,
    account: Account,
    reference_capital: Decimal = Decimal("1000000"),
    model_name: str | None = None,
    console: Console | None = None,
) -> dict:
    
    if reference_capital <= 0:
        raise PortfolioContractError(
            code="portfolio.comparison_reference_invalid",
            message=f"参考资金必须为正数，收到 {reference_capital}",
            blocking=True,
            suggested_action="--reference-capital 传入正数金额，如 1000000",
        )
    _path, raw, _bucket = PortfolioStore().find_plan(plan_id, primary_account=account.account_id)
    profile_name = str(raw.get("strategy_profile_ref") or "")
    if not profile_name:
        raise PortfolioContractError(
            code="portfolio.comparison_no_strategy",
            message=f"计划 {plan_id} 未记录 strategy_profile_ref，无法做资金对比",
            blocking=True,
            suggested_action="仅规则引擎生成的计划支持资金口径对比",
        )

    start, end = _window()
    account_capital = _account_capital(account)

    reference_run = _run_once(
        profile_name, start=start, end=end, capital=reference_capital, console=console
    )
    account_run = _run_once(
        profile_name, start=start, end=end, capital=account_capital, console=console
    )
    deltas = _metric_deltas(reference_run.metrics, account_run.metrics)

    ai: dict = {"ok": False, "model": None, "summary": None, "shortfall_causes": [], "error": ""}
    try:
        ai = _ai_summary(
            plan_id=plan_id,
            profile_name=profile_name,
            window=(start, end),
            reference_capital=reference_capital,
            account_capital=account_capital,
            reference_metrics=reference_run.metrics,
            account_metrics=account_run.metrics,
            deltas=deltas,
            model_name=model_name,
        )
    except QuantTrainingError as exc:
        ai["error"] = f"{exc.code}: {exc.message}"
    except Exception as exc:  
        ai["error"] = f"{type(exc).__name__}: {exc}"

    payload = {
        "plan_id": plan_id,
        "account_id": account.account_id,
        "strategy_profile": profile_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {"start": start, "end": end},
        "reference_capital": str(reference_capital),
        "account_capital": str(account_capital),
        "reference_run_id": reference_run.run_id,
        "account_run_id": account_run.run_id,
        "metrics": {
            "reference": reference_run.metrics,
            "account": account_run.metrics,
            "delta": deltas,
        },
        "ai": ai,
        "note": "历史模拟执行落差对比（同窗口、不同初始资金），非未来收益预测",
    }
    target = _comparison_path(account, plan_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(target)
    return payload

def load_comparison(*, plan_id: str, account: Account) -> dict:
    
    target = _comparison_path(account, plan_id)
    if not target.exists():
        raise PortfolioContractError(
            code="portfolio.comparison_not_found",
            message=f"计划 {plan_id} 尚无资金口径对比结果",
            context={"plan_id": plan_id, "account_id": account.account_id},
            blocking=True,
            suggested_action=(
                f"先执行 `recommend compare {plan_id} --account {account.account_id}` 生成对比"
            ),
        )
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}

def comparison_exists(*, plan_id: str, account: Account) -> bool:
    return _comparison_path(account, plan_id).exists()

__all__ = ["comparison_exists", "load_comparison", "run_capital_comparison"]
