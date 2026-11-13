from __future__ import annotations

import secrets
import hashlib
import re
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from exceptions import ContractViolation, PortfolioContractError
from portfolio import Account, AccountRegistry, Cash, PortfolioStore
from portfolio.models import (
    Action,
    PlanState,
    Side,
    Trade,
    TradeStatus,
    dump_decimals,
)
from portfolio.trades import (
    entry_apply_trade,
    holdings_to_positions,
    positions_to_holdings_df,
)
from portfolio.permissions import can_buy

from ..schemas import ConfirmPayload, confirm_preview, parse_enum
from ..serialize import df_records, json_safe

router = APIRouter(tags=["portfolio-write"])


def _stock_code(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[036489]\d{5}", value):
        raise ContractViolation(code="QL_INVALID_STOCK_CODE", message="请填写六位股票代码。")
    return value


def _invalidate_plans(account_id: str) -> None:
    store = PortfolioStore()
    for _path, raw in store.list_active_plans(primary_account=account_id):
        if raw.get("state") == PlanState.ACTIVE.value:
            store.transition_plan(str(raw["plan_id"]), primary_account=account_id,
                                  new_state=PlanState.STALE_FOR_EXECUTION, confirm=True)


def _plan_files(account_id: str) -> list:
    return [path for path, _raw in PortfolioStore().list_active_plans(primary_account=account_id)]

def _dec(raw: str, field: str, *, positive: bool = False) -> Decimal:
    field={"qty":"数量","price":"价格","cost_price":"成本价","fee":"费用","tax":"税费","other_cost":"其他费用","total":"总额","available":"可用金额","withdrawable":"可取金额","frozen":"冻结金额"}.get(field,field)
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_DECIMAL",
            message=f"{field} 不是合法数字：{raw!r}。",
        ) from exc
    if not value.is_finite():
        raise ContractViolation(
            code="WEBUI_INVALID_DECIMAL", message=f"{field} 必须是有限数字：{raw!r}。"
        )
    if positive and value <= 0:
        raise ContractViolation(code="WEBUI_INVALID_DECIMAL", message=f"{field} 必须大于 0。")
    if not positive and value < 0:
        raise ContractViolation(code="WEBUI_INVALID_DECIMAL", message=f"{field} 不允许为负数。")
    return value

def _parse_executed_at(raw: str) -> datetime:
    if not raw.strip():
        return datetime.now(UTC)
    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_DATETIME",
            message=f"成交时间不是合法 ISO8601：{raw!r}。",
            suggested_action="示例：2026-08-04T10:30:00+08:00；缺省时区按北京时间。",
        ) from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return value.astimezone(UTC)

# 账户

class AccountCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: bool = False
    account_id: str = ""
    alias: str = Field(min_length=1)
    market: str = ""
    instrument_class: str = ""
    reporting_currency: str = "CNY"
    broker: str = ""
    cash_currency: str = ""
    notes: str = ""
    star_market_enabled: bool = False

@router.post("/accounts")
def create_account(payload: AccountCreatePayload) -> dict:
    from portfolio.transaction import lock
    with lock:
        from session import has_active_market, load_session

        market, instrument_class = payload.market.strip(), payload.instrument_class.strip()
        if not market or not instrument_class:
            session = load_session()
            if not has_active_market(session):
                raise ContractViolation(
                    code="WEBUI_ACCOUNT_MISSING_MARKET",
                    message="尚未设置交易市场。",
                    suggested_action="请恢复默认 A 股配置。",
                )
            market = market or session.active_market
            instrument_class = instrument_class or session.active_instrument_class

        if market!='CN-A' or instrument_class!='stock' or payload.reporting_currency.upper()!='CNY' or payload.cash_currency.upper() not in {'','CNY'}:
            raise ContractViolation(code='QL_A_SHARE_ACCOUNT_REQUIRED',message='仅支持人民币 A 股账户',suggested_action='使用 A 股账户默认设置')
        currency = (payload.cash_currency or payload.reporting_currency).upper()
        zero = Decimal("0")
        try:
            account = Account(
                account_id=payload.account_id or f"acc-{secrets.token_hex(10)}",
                alias=payload.alias,
                market=market,
                instrument_class=instrument_class,
                reporting_currency=payload.reporting_currency.upper(),
                broker=payload.broker,
                star_market_enabled=payload.star_market_enabled,
                cash=[
                    Cash(currency=currency, total=zero, available=zero, withdrawable=zero, frozen=zero)
                ],
                created_at=datetime.now(UTC),
                reinit_count=0,
                notes=payload.notes,
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ContractViolation(
                code="WEBUI_INVALID_ACCOUNT",
                message=f"账户字段校验失败：{first.get('loc', ('?',))[0]} — {first.get('msg', '')}",
            ) from exc

        if not payload.confirm:
            return confirm_preview(
                {
                    "操作": "创建账户",
                    "账户名称": account.alias,
                    "科创板权限": "已开通" if account.star_market_enabled else "未开通（不推荐买入科创板股票）",
                    "初始现金": "0 元",
                    "券商": payload.broker or "（未填）",
                    "备注": payload.notes,
                }
            )
        AccountRegistry().create(account)
        return json_safe({"account": AccountRegistry().get(account.account_id)})


class AccountSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: bool = False
    alias: str = Field(min_length=1)
    broker: str = ""
    notes: str = ""
    star_market_enabled: bool = False


@router.post("/accounts/{account_id}/settings")
def update_account_settings(account_id: str, payload: AccountSettingsPayload) -> dict:
    from portfolio.transaction import lock
    with lock:
        registry = AccountRegistry()
        account = registry.get(account_id)
        if not payload.confirm:
            return confirm_preview({"操作": "修改账户", "账户名称": payload.alias,
                                    "券商": payload.broker or "（未填）",
                                    "备注": payload.notes,
                                    "科创板权限": "已开通" if payload.star_market_enabled else "未开通"})
        if account.star_market_enabled != payload.star_market_enabled:
            from lifecycle.models import ObjectType
            from lifecycle.states import ActorType
            from lifecycle.store import LifecycleStore
            lifecycle = LifecycleStore()
            for row in lifecycle.list_objects(ObjectType.PORTFOLIO_PLAN, limit=5000):
                if row["state"] in {"proposed", "confirmed", "active", "partially_executed"}:
                    plan = lifecycle.get(ObjectType.PORTFOLIO_PLAN, row["object_id"])
                    if account_id in plan.account_ids:
                        lifecycle.transition(ObjectType.PORTFOLIO_PLAN, row["object_id"], "expired",
                                             actor_type=ActorType.SYSTEM, actor_ref="account_settings",
                                             reason="账户交易权限已变更，请重新生成计划")
            store = PortfolioStore()
            for _path, raw in store.list_active_plans(primary_account=account_id):
                if raw.get("state") == PlanState.ACTIVE.value:
                    store.transition_plan(str(raw["plan_id"]), primary_account=account_id,
                                          new_state=PlanState.STALE_FOR_EXECUTION, confirm=True)
        account = account.model_copy(update=payload.model_dump(exclude={"confirm"}))
        registry.update(account)
        return json_safe({"account": account})

@router.delete("/accounts/{account_id}")
def delete_account(account_id: str, payload: ConfirmPayload) -> dict:
    from portfolio.transaction import lock, transaction
    with lock:
        registry = AccountRegistry()
        account = registry.get(account_id)
        if not payload.confirm:
            return confirm_preview(
                {
                    "操作": "删除账户",
                    "账户": account.alias or account.account_id,
                    "删除范围": "账户资料、持仓、成交、快照和账户计划。",
                    "说明": "删除后从账户列表和每日调度中移除，未完成计划失效。账户数据移入回收站保留。",
                }
            )
        from automation import AutomationStore
        from lifecycle.models import ObjectType
        from lifecycle.states import ActorType
        from lifecycle.store import LifecycleStore

        lifecycle = LifecycleStore()
        transitions = []
        for row in lifecycle.list_objects(ObjectType.PORTFOLIO_PLAN, limit=5000):
            if row["state"] in {"proposed", "confirmed", "active", "partially_executed"}:
                plan = lifecycle.get(ObjectType.PORTFOLIO_PLAN, row["object_id"])
                if account_id in plan.account_ids:
                    transitions.append({"object_type": ObjectType.PORTFOLIO_PLAN,
                                        "object_id": row["object_id"], "target": "expired",
                                        "actor_type": ActorType.SYSTEM, "actor_ref": "account_delete",
                                        "reason": "账户已删除"})
        if transitions:
            lifecycle.transition_batch(transitions)
        automation = AutomationStore()
        config = automation.load()
        with transaction([automation.path]):
            if account_id in config.account_ids:
                remaining = [value for value in config.account_ids if value != account_id]
                automation.save(config.model_copy(update={"account_ids": remaining,
                                                         "enabled": config.enabled and bool(remaining)}))
            registry.delete(account_id, confirm=True)
        return {"deleted": account_id}

class AccountCashPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    currency: str = ""
    total: str
    available: str = ""
    withdrawable: str = ""
    frozen: str = "0"

@router.post("/accounts/{account_id}/cash")
def set_account_cash(account_id: str, payload: AccountCashPayload) -> dict:
    from portfolio.transaction import lock, transaction
    with lock:
        registry = AccountRegistry()
        account = registry.get(account_id)
        currency = (payload.currency or account.reporting_currency).strip().upper()
        if currency!='CNY':
            raise ContractViolation(
                code="WEBUI_INVALID_CURRENCY",
                message=f"币种仅支持人民币（CNY）：{payload.currency!r}。",
            )
        total = _dec(payload.total, "total")
        available = _dec(payload.available, "available") if payload.available.strip() else total
        withdrawable = (
            _dec(payload.withdrawable, "withdrawable") if payload.withdrawable.strip() else available
        )
        frozen = _dec(payload.frozen, "frozen")
        if available > total or withdrawable > available:
            raise ContractViolation(
                code="WEBUI_CASH_BUCKET_INCONSISTENT",
                message=(
                    f"现金桶不自洽：须满足 withdrawable({withdrawable}) ≤ "
                    f"available({available}) ≤ total({total})。"
                ),
            )
        old = next((c for c in account.cash if c.currency == currency), None)
        if not payload.confirm:
            return confirm_preview(
                {
                    "操作": "调整现金",
                    "账户": account.alias or account.account_id,
                    "原余额": str(old.total) if old else "0",
                    "新余额": str(total),
                    "可用金额": str(available),
                }
            )
        account.cash = [c for c in account.cash if c.currency != currency] + [
            Cash(
                currency=currency,
                total=total,
                available=available,
                withdrawable=withdrawable,
                frozen=frozen,
            )
        ]
        directory = registry.ws.portfolio_account(account.market, account.instrument_class, account_id)
        with transaction([registry.ws.portfolio_index, directory / "account.yaml", *_plan_files(account_id)]):
            registry.update(account)
            _invalidate_plans(account_id)
        return json_safe({"account": registry.get(account_id)})

# 成交

class TradeAddPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    account: str
    instrument_id: str
    side: Literal["long"] = "long"
    action: str
    qty: str
    price: str
    currency: Literal["CNY"] = "CNY"
    executed_at: str = ""
    note: str = ""
    request_id: str = ""

@router.post("/portfolio/trades/add")
def trades_add(payload: TradeAddPayload) -> dict:
    from portfolio.transaction import lock
    with lock:
        import pandas as pd

        acc = AccountRegistry().get(payload.account)
        store = PortfolioStore()

        instrument_id = _stock_code(payload.instrument_id)
        side_v = parse_enum(Side, payload.side, "side")
        qty_v = _dec(payload.qty, "qty", positive=True)
        price_v = _dec(payload.price, "price", positive=True)
        currency_v = (payload.currency or acc.reporting_currency).strip().upper()
        if len(currency_v) != 3 or not currency_v.isalpha():
            raise ContractViolation(
                code="WEBUI_INVALID_CURRENCY",
                message=f"币种仅支持人民币（CNY）：{payload.currency!r}。",
            )
        executed_v = _parse_executed_at(payload.executed_at)

        holdings_df = store.read_holdings(
            acc.account_id, market=acc.market, instrument_class=acc.instrument_class
        )
        positions = holdings_to_positions(holdings_df)
        before_position = deepcopy(positions.get((instrument_id, "long")))
        if payload.action == "buy":
            action_v = Action.INCREASE if before_position else Action.OPEN
        elif payload.action == "sell":
            action_v = Action.CLOSE if before_position and before_position["qty"] == qty_v else Action.DECREASE
        else:
            action_v = parse_enum(Action, payload.action, "action")
        if action_v in {Action.OPEN, Action.INCREASE} and not can_buy(instrument_id, star_market_enabled=acc.star_market_enabled):
            raise ContractViolation(code="QL_STAR_MARKET_PERMISSION_REQUIRED", message="该账户未开通科创板权限，无法录入该股票的买入。")
        from portfolio.adapters import get_adapter
        costs = get_adapter(acc.market, acc.instrument_class).compute_costs(
            side=side_v, action=action_v, qty=qty_v, price=price_v, currency=currency_v)
        fee_v, tax_v, other_v = costs.fee, costs.tax, costs.other_cost
        if side_v is not Side.LONG or currency_v!='CNY' or qty_v!=qty_v.to_integral_value():
            raise ContractViolation(code='QL_A_SHARE_TRADE_REQUIRED',message='当前只记录人民币 A 股整数股的多头成交',suggested_action='核对方向、币种和数量')
        prior=store.read_trades(acc.account_id,market=acc.market,instrument_class=acc.instrument_class, include_reversed=True)
        ledger = store.read_trade_ledger(acc.account_id, market=acc.market, instrument_class=acc.instrument_class)
        trade_id=f'TRD-{hashlib.sha256(payload.request_id.encode()).hexdigest()[:32]}' if payload.request_id else f'TRD-{executed_v:%Y%m%d}-{secrets.token_hex(3)}'
        if not prior.empty and trade_id in set(prior.trade_id):
            previous=prior[prior.trade_id==trade_id].iloc[0]
            numeric={'qty':qty_v,'price':price_v}
            same_action = previous.action == action_v.value
            if payload.action in {"buy", "sell"}:
                same_action = (previous.action in {"open", "increase"}) == (payload.action == "buy")
            if previous.instrument_id!=instrument_id or not same_action or previous.side!=side_v.value or previous.currency!=currency_v or (payload.executed_at and pd.Timestamp(previous.executed_at)!=pd.Timestamp(executed_v)) or any(Decimal(str(previous[k]))!=v for k,v in numeric.items()):
                raise ContractViolation(code='QL_REQUEST_REUSED',message='同一提交编号对应了不同成交',suggested_action='重新打开录入表单')
            return {'trade_id':trade_id,'duplicate':True, 'reversed': trade_id in ledger.get("reversals", {})}
        prior = prior[~prior["trade_id"].isin(ledger.get("reversals", {}))]
        if not prior.empty and pd.to_datetime(prior.executed_at,utc=True).max()>executed_v:
            raise ContractViolation(code='QL_TRADE_ORDER',message='成交时间早于已有记录',suggested_action='请按时间顺序录入，或先撤回上一笔交易。')
        cash=next((c for c in acc.cash if c.currency==currency_v),None)
        delta=qty_v*price_v-fee_v-tax_v-other_v if action_v in {Action.CLOSE,Action.DECREASE} else -(qty_v*price_v+fee_v+tax_v+other_v)
        if cash is None or cash.available+delta<0:
            raise ContractViolation(code='QL_CASH_INSUFFICIENT',message='可用现金不足',suggested_action='先核对并设置账户现金')
        cash_after=cash.model_copy(update={'total':cash.total+delta,'available':cash.available+delta,
            'withdrawable':min(cash.withdrawable,cash.available+delta)})
        entry_apply_trade(
            positions,
            account=acc,
            instrument_id=instrument_id,
            side=side_v,
            action=action_v,
            qty=qty_v,
            price=price_v,
            currency=currency_v,
            as_of=executed_v.isoformat(),
        )
        new_df = positions_to_holdings_df(positions, account=acc)

        provisional = Trade(
            trade_id=trade_id,
            account_id=acc.account_id,
            executed_at=executed_v,
            market=acc.market,
            instrument_class=acc.instrument_class,
            instrument_id=instrument_id,
            code=instrument_id,
            side=side_v,
            action=action_v,
            qty=qty_v,
            price=price_v,
            fee=fee_v,
            tax=tax_v,
            other_cost=other_v,
            currency=currency_v,
            status=TradeStatus.UNPLANNED,
            note=payload.note,
        )
        if not payload.confirm:
            result = confirm_preview(
                {
                    "操作": "录入单笔成交并同步持仓",
                    "账户": f"{acc.account_id}（{acc.alias}）",
                    "成交": f"{'买入' if action_v in {Action.OPEN, Action.INCREASE} else '卖出'} {instrument_id}，{qty_v} 股，成交价 {price_v} 元",
                    "手续费": str(fee_v),
                    "印花税": str(tax_v),
                    "费用合计": str(fee_v + tax_v + other_v),
                    "成交时间": executed_v.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
                    "成交后现金": str(cash_after.total),
                    "成交后持仓条数": str(len(new_df)),
                }
            )
            result["holdings_after"] = df_records(new_df)
            return result

        try:
            trade = provisional
        except ValidationError as exc:
            raise PortfolioContractError(
                code="portfolio.trade_invalid",
                message=f"成交记录校验失败：{exc.errors()[0].get('msg', exc)}",
                blocking=True,
            ) from exc

        from paths import workspace
        from portfolio.transaction import transaction
        ws=workspace()
        account_dir=ws.portfolio_account(acc.market,acc.instrument_class,acc.account_id)
        files=[ws.portfolio_index,account_dir/'account.yaml',ws.portfolio_holdings(acc.market,acc.instrument_class,acc.account_id)/'current.parquet',
               ws.portfolio_trades(acc.market,acc.instrument_class,acc.account_id)/f'{executed_v:%Y-%m}.parquet',
               store.trade_ledger_path(acc.account_id, market=acc.market, instrument_class=acc.instrument_class), *_plan_files(acc.account_id)]
        trade_payload=dump_decimals(trade)
        with transaction(files):
            ledger.setdefault("entries", {})[trade_id] = json_safe({
                "position_before": before_position,
                "position_after": positions.get((instrument_id, "long")),
                "cash_before": cash.model_dump(mode="json"),
                "cash_after": cash_after.model_dump(mode="json"),
            })
            store.append_trades(acc.account_id,pd.DataFrame([trade_payload],columns=list(trade_payload)),market=acc.market,instrument_class=acc.instrument_class)
            store.write_holdings(acc.account_id,new_df,market=acc.market,instrument_class=acc.instrument_class)
            acc.cash=[c for c in acc.cash if c.currency!=currency_v]+[cash_after]
            AccountRegistry().update(acc)
            store.write_trade_ledger(acc.account_id, ledger, market=acc.market, instrument_class=acc.instrument_class)
            _invalidate_plans(acc.account_id)
        return json_safe({'trade_id':trade.trade_id,'holdings_rows':len(new_df),
            'cash_after':cash_after.total})


class HoldingSetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: bool = False
    instrument_id: str
    qty: str
    cost_price: str = "0"


@router.put("/portfolio/{account_id}/holdings")
def set_holding(account_id: str, payload: HoldingSetPayload) -> dict:
    from portfolio.transaction import lock, transaction
    from portfolio.adapters import get_adapter
    from portfolio.trades import refresh_available_qty

    with lock:
        registry = AccountRegistry()
        account = registry.get(account_id)
        instrument_id = _stock_code(payload.instrument_id)
        qty = _dec(payload.qty, "qty")
        cost = _dec(payload.cost_price, "cost_price", positive=qty > 0)
        if qty != qty.to_integral_value():
            raise ContractViolation(code="QL_INVALID_HOLDING_QTY", message="持仓数量必须为非负整数股。")
        store = PortfolioStore()
        positions = holdings_to_positions(store.read_holdings(account_id, market=account.market, instrument_class=account.instrument_class))
        old = positions.get((instrument_id, "long"))
        if not payload.confirm:
            return confirm_preview({"操作": "录入持仓", "股票代码": instrument_id,
                                    "原持仓": str(old["qty"]) if old else "0",
                                    "新持仓": str(qty), "成本价": str(cost),
                                    "说明": "替换这只股票的持仓，不改变现金或成交记录；冻结数量按历史成交自动计算。"})
        if qty:
            positions[(instrument_id, "long")] = {"qty": qty, "available_qty": qty,
                "cost_price": cost, "currency": "CNY", "as_of": datetime.now(UTC).isoformat()}
        else:
            positions.pop((instrument_id, "long"), None)
        trades = store.read_trades(account_id, market=account.market, instrument_class=account.instrument_class)
        refresh_available_qty(positions, trades, adapter=get_adapter(account.market, account.instrument_class),
                              as_of=datetime.now(ZoneInfo("Asia/Shanghai")).date())
        holdings_path = store.ws.portfolio_holdings(account.market, account.instrument_class, account_id) / "current.parquet"
        with transaction([holdings_path, *_plan_files(account_id)]):
            store.write_holdings(account_id, positions_to_holdings_df(positions, account=account),
                                 market=account.market, instrument_class=account.instrument_class)
            _invalidate_plans(account_id)
        return {"instrument_id": instrument_id, "qty": str(qty)}


class TradeUndoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    trade_id: str


@router.post("/portfolio/{account_id}/trades/undo")
def undo_trade(account_id: str, payload: TradeUndoPayload) -> dict:
    from portfolio.transaction import lock, transaction
    from portfolio.adapters import get_adapter
    from portfolio.trades import refresh_available_qty

    with lock:
        registry = AccountRegistry()
        account = registry.get(account_id)
        store = PortfolioStore()
        scope = {"market": account.market, "instrument_class": account.instrument_class}
        ledger = store.read_trade_ledger(account_id, **scope)
        if payload.trade_id in ledger.get("reversals", {}):
            return {"undone": payload.trade_id, "duplicate": True}
        trades = store.read_trades(account_id, **scope)
        if trades.empty:
            raise ContractViolation(code="QL_NO_TRADE_TO_UNDO", message="没有可撤回的交易。")
        latest = trades.sort_values("executed_at", kind="stable").iloc[-1]
        if latest.trade_id != payload.trade_id:
            raise ContractViolation(code="QL_TRADE_NOT_LATEST", message="只能撤回最近一笔未撤回的交易，请刷新交易记录。")
        entry = ledger.get("entries", {}).get(payload.trade_id)
        if not entry:
            raise ContractViolation(code="QL_UNDO_UNAVAILABLE", message="该笔历史交易缺少撤回所需的持仓记录，无法自动撤回。")
        positions = holdings_to_positions(store.read_holdings(account_id, **scope))
        key = (str(latest.instrument_id), "long")
        current = positions.get(key)
        expected = entry["position_after"]
        if bool(current) != bool(expected) or (current and any(
            Decimal(str(current[name])) != Decimal(str(expected[name])) for name in ("qty", "cost_price"))):
            raise ContractViolation(code="QL_UNDO_HOLDING_CHANGED", message="该股票的持仓在成交后被手动修改，无法直接撤回。")
        currency = str(latest.currency)
        cash = next((bucket for bucket in account.cash if bucket.currency == currency), None)
        if cash is None:
            raise ContractViolation(code="QL_UNDO_CASH_MISSING", message="账户现金记录缺失，无法撤回。")
        cash_values = {name: getattr(cash, name) + Decimal(entry["cash_before"][name]) - Decimal(entry["cash_after"][name])
                       for name in ("total", "available", "withdrawable", "frozen")}
        if any(value < 0 for value in cash_values.values()) or not cash_values["withdrawable"] <= cash_values["available"] <= cash_values["total"]:
            raise ContractViolation(code="QL_UNDO_CASH_INSUFFICIENT", message="当前余额不足以撤回这笔卖出交易，请先核对账户资金。")
        restored_cash = cash.model_copy(update=cash_values)
        refund = restored_cash.total - cash.total
        if not payload.confirm:
            return confirm_preview({"操作": "撤回上一笔交易", "股票代码": str(latest.instrument_id),
                                    "成交方向": "买入" if latest.action in {"open", "increase"} else "卖出",
                                    "成交数量": str(latest.qty), "余额变化": str(refund),
                                    "撤回后余额": str(restored_cash.total),
                                    "说明": "恢复成交前的持仓和资金，手续费一并回退。原交易记录保留并标记为已撤回。"})
        before = entry["position_before"]
        if before is None:
            positions.pop(key, None)
        else:
            positions[key] = {**before, **{name: Decimal(str(before[name])) for name in ("qty", "available_qty", "cost_price")}}
        remaining = trades[trades["trade_id"] != payload.trade_id]
        refresh_available_qty(positions, remaining, adapter=get_adapter(account.market, account.instrument_class),
                              as_of=datetime.now(ZoneInfo("Asia/Shanghai")).date())
        ledger.setdefault("reversals", {})[payload.trade_id] = {"reversed_at": datetime.now(UTC).isoformat(), "balance_change": str(refund)}
        account.cash = [bucket for bucket in account.cash if bucket.currency != currency] + [restored_cash]
        directory = store.ws.portfolio_account(account.market, account.instrument_class, account_id)
        files = [store.ws.portfolio_index, directory / "account.yaml",
                 store.ws.portfolio_holdings(account.market, account.instrument_class, account_id) / "current.parquet",
                 store.trade_ledger_path(account_id, **scope), *_plan_files(account_id)]
        with transaction(files):
            store.write_holdings(account_id, positions_to_holdings_df(positions, account=account), **scope)
            registry.update(account)
            store.write_trade_ledger(account_id, ledger, **scope)
            _invalidate_plans(account_id)
        return {"undone": payload.trade_id, "cash_after": str(restored_cash.total)}
