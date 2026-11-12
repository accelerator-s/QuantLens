from __future__ import annotations

import secrets
import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, ValidationError

from exceptions import ContractViolation, PortfolioContractError
from portfolio import Account, AccountRegistry, Cash, Plan, PortfolioStore, Snapshot
from portfolio.models import (
    HOLDING_COLUMNS,
    Action,
    PlanState,
    Side,
    SnapshotMode,
    Trade,
    TradeStatus,
    dump_decimals,
)
from portfolio.snapshots import base_rows_from_holdings, base_rows_from_snapshot, build_snapshot
from portfolio.trades import (
    entry_apply_trade,
    holdings_to_positions,
    last_reinit_at,
    positions_to_holdings_df,
    replay_trades,
)

from ..schemas import ConfirmPayload, confirm_preview, parse_enum
from ..serialize import df_records, json_safe

router = APIRouter(tags=["portfolio-write"])

def _dec(raw: str, field: str, *, positive: bool = False) -> Decimal:
    field={"qty":"数量","price":"价格","fee":"费用","tax":"税费","other_cost":"其他费用","total":"总额","available":"可用金额","withdrawable":"可取金额","frozen":"冻结金额"}.get(field,field)
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

def _parse_yaml(text: str, label: str):
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_YAML",
            message=f"{label} YAML 解析失败：{exc}",
        ) from exc

# 账户

class AccountCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    account_id: str
    alias: str
    market: str = ""
    instrument_class: str = ""
    reporting_currency: str = "CNY"
    broker: str = ""
    cash_currency: str = ""
    notes: str = ""

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
                account_id=payload.account_id,
                alias=payload.alias,
                market=market,
                instrument_class=instrument_class,
                reporting_currency=payload.reporting_currency.upper(),
                broker=payload.broker,
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
                    "账户编号 / 别名": f"{payload.account_id} / {payload.alias}",
                    "市场 / 品种": f"{market} / {instrument_class}",
                    "报告币种": payload.reporting_currency.upper(),
                    "初始现金": f"{currency} 0",
                    "券商": payload.broker or "（未填）",
                }
            )
        AccountRegistry().create(account)
        return json_safe({"account": AccountRegistry().get(payload.account_id)})

@router.post("/accounts/{account_id}/archive")
def archive_account(account_id: str, payload: ConfirmPayload) -> dict:
    from portfolio.transaction import lock
    with lock:
        registry = AccountRegistry()
        account = registry.get(account_id)
        if not payload.confirm:
            return confirm_preview(
                {
                    "操作": "归档账户（整体移入回收站）",
                    "账户": f"{account.account_id}（{account.alias}）",
                    "市场 / 品种": f"{account.market} / {account.instrument_class}",
                    "说明": "归档后持仓、成交、快照等数据整体移入 trash，可在回收站找回。",
                }
            )
        registry.archive(account_id, confirm=True)
        return {"archived": account_id}

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
    from portfolio.transaction import lock
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
                    "操作": f"设置 {currency} 现金桶（整桶覆盖）",
                    "账户": f"{account.account_id}（{account.alias}）",
                    "原现金": f"total {old.total} / available {old.available}" if old else "无",
                    "新现金": (
                        f"total {total} / available {available} / "
                        f"withdrawable {withdrawable} / frozen {frozen}"
                    ),
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
        registry.update(account)
        return json_safe({"account": registry.get(account_id)})

# 快照

class SnapshotSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    yaml_text: str
    mode: str = "detailed"

@router.post("/portfolio/snapshot/submit")
def snapshot_submit(payload: SnapshotSubmitPayload) -> dict:
    from portfolio.transaction import lock
    with lock:
        raw = _parse_yaml(payload.yaml_text, "快照")
        if not isinstance(raw, dict):
            raise ContractViolation(code="WEBUI_INVALID_YAML", message="快照 YAML 顶层必须是映射。")
        raw.setdefault("mode", payload.mode)
        try:
            snap = Snapshot.model_validate(raw)
        except ValidationError as exc:
            raise ContractViolation(
                code="WEBUI_INVALID_SNAPSHOT",
                message=f"快照字段校验失败：{exc.errors()[0].get('msg', '')}",
            ) from exc
        account = AccountRegistry().get(snap.account_id)
        if not payload.confirm:
            return confirm_preview(
                {
                    "操作": "提交持仓快照",
                    "快照 ID": snap.snapshot_id,
                    "账户": f"{account.account_id}（{account.alias}）",
                    "模式": snap.mode.value,
                    "仓位数": str(len(snap.positions)),
                    "总资产": f"{snap.totals_reporting.total_assets} {account.reporting_currency}",
                }
            )
        path = PortfolioStore().write_snapshot(
            snap, market=account.market, instrument_class=account.instrument_class
        )
        return json_safe({"snapshot_id": snap.snapshot_id, "path": str(path)})

class SnapshotCommitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    account: str
    mode: str = "detailed"
    note: str = ""
    prices: dict[str, str] = {}

@router.post("/portfolio/snapshot/commit")
def snapshot_commit(payload: SnapshotCommitPayload) -> dict:
    registry = AccountRegistry()
    acc = registry.get(payload.account)
    store = PortfolioStore()
    snap_mode = parse_enum(SnapshotMode, payload.mode, "mode")

    if snap_mode is SnapshotMode.DETAILED:
        df = store.read_holdings(
            acc.account_id, market=acc.market, instrument_class=acc.instrument_class
        )
        base_rows = base_rows_from_holdings(df)
    else:
        latest = store.latest_snapshot(
            acc.account_id, market=acc.market, instrument_class=acc.instrument_class
        )
        if latest is None:
            raise PortfolioContractError(
                code="portfolio.snapshot_unchanged_without_base",
                message="该账户暂无快照，不能沿用上次快照。",
                blocking=True,
                suggested_action="请先选择“当前持仓”生成快照。",
            )
        base_rows = base_rows_from_snapshot(latest)

    prices = {
        k: _dec(v, f"{k} 的现价", positive=True)
        for k, v in payload.prices.items()
        if str(v).strip()
    }
    missing = [r["instrument_id"] for r in base_rows if r["instrument_id"] not in prices]

    if not payload.confirm:
        result = confirm_preview(
            {
                "操作": "提交持仓快照（表单）",
                "账户": f"{acc.account_id}（{acc.alias}）",
                "模式": snap_mode.value,
                "仓位数": str(len(base_rows)),
                "待填现价": "、".join(missing) or "（已齐）",
                "说明": "确认前需为每个持仓填写现价；无持仓时提交仅含现金的快照。",
            }
        )
        result["base_rows"] = json_safe(
            [
                {
                    "instrument_id": r["instrument_id"],
                    "currency": r["currency"],
                    "price": str(prices[r["instrument_id"]])
                    if r["instrument_id"] in prices
                    else "",
                }
                for r in base_rows
            ]
        )
        return result

    if missing:
        raise ContractViolation(
            code="WEBUI_SNAPSHOT_PRICES_MISSING",
            message=f"以下持仓缺少现价：{'、'.join(missing)}。",
        )
    snap = build_snapshot(
        account=acc,
        mode=snap_mode,
        base_rows=base_rows,
        prices=prices,
        taken_at=datetime.now(UTC),
        note=payload.note,
    )
    path = store.write_snapshot(snap, market=acc.market, instrument_class=acc.instrument_class)
    return json_safe(
        {
            "snapshot_id": snap.snapshot_id,
            "path": str(path),
            "positions": len(snap.positions),
            "total_assets": str(snap.totals_reporting.total_assets),
            "floating_pnl": str(snap.totals_reporting.floating_pnl),
        }
    )

# 成交

class TradeAddPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    account: str
    instrument_id: str
    side: str
    action: str
    qty: str
    price: str
    fee: str = "0"
    tax: str = "0"
    other_cost: str = "0"
    currency: str = ""
    executed_at: str = ""
    related_plan_id: str = ""
    note: str = ""
    request_id: str = ""

@router.post("/portfolio/trades/add")
def trades_add(payload: TradeAddPayload) -> dict:
    from portfolio.transaction import lock
    with lock:
        import pandas as pd

        acc = AccountRegistry().get(payload.account)
        store = PortfolioStore()

        instrument_id = payload.instrument_id.strip()
        if not instrument_id:
            raise ContractViolation(code="WEBUI_MISSING_FIELD", message="请填写证券代码。")
        side_v = parse_enum(Side, payload.side, "side")
        action_v = parse_enum(Action, payload.action, "action")
        qty_v = _dec(payload.qty, "qty", positive=True)
        price_v = _dec(payload.price, "price", positive=True)
        fee_v = _dec(payload.fee, "fee")
        tax_v = _dec(payload.tax, "tax")
        other_v = _dec(payload.other_cost, "other_cost")
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

        related_plan_id = payload.related_plan_id.strip()
        if side_v is not Side.LONG or currency_v!='CNY' or qty_v!=qty_v.to_integral_value():
            raise ContractViolation(code='QL_A_SHARE_TRADE_REQUIRED',message='当前只记录人民币 A 股整数股的多头成交',suggested_action='核对方向、币种和数量')
        prior=store.read_trades(acc.account_id,market=acc.market,instrument_class=acc.instrument_class)
        trade_id=f'TRD-{hashlib.sha256(payload.request_id.encode()).hexdigest()[:32]}' if payload.request_id else f'TRD-{executed_v:%Y%m%d}-{secrets.token_hex(3)}'
        if not prior.empty and trade_id in set(prior.trade_id):
            previous=prior[prior.trade_id==trade_id].iloc[0]
            numeric={'qty':qty_v,'price':price_v,'fee':fee_v,'tax':tax_v,'other_cost':other_v}
            if previous.instrument_id!=instrument_id or previous.action!=action_v.value or previous.side!=side_v.value or previous.currency!=currency_v or (payload.executed_at and pd.Timestamp(previous.executed_at)!=pd.Timestamp(executed_v)) or any(Decimal(str(previous[k]))!=v for k,v in numeric.items()):
                raise ContractViolation(code='QL_REQUEST_REUSED',message='同一提交编号对应了不同成交',suggested_action='重新打开录入表单')
            if related_plan_id and payload.confirm:
                from lifecycle.execution import ExecutionFeedbackService
                from portfolio.recommend import holdings_digest
                ExecutionFeedbackService().record(Trade.model_validate(previous.to_dict()),related_plan_id,
                    holding_snapshot_ref=f'portfolio://{acc.account_id}/holdings/{holdings_digest(holdings_df)}')
            return {'trade_id':trade_id,'duplicate':True}
        if not prior.empty and pd.to_datetime(prior.executed_at,utc=True).max()>executed_v:
            raise ContractViolation(code='QL_TRADE_ORDER',message='成交时间早于已有记录',suggested_action='请按时间顺序录入；历史纠错使用成交修正')
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
            related_plan_id=related_plan_id,
            note=payload.note,
        )
        from lifecycle.execution import ExecutionFeedbackService

        classification, match_reason = ExecutionFeedbackService().classify(provisional,related_plan_id) if related_plan_id else ('unplanned','用户主动成交，未关联策略计划')
        match_status = {
            "planned": TradeStatus.PLANNED,
            "partial": TradeStatus.PARTIAL,
            "large_deviation": TradeStatus.PLANNED_WITH_LARGE_DEVIATION,
            "unplanned": TradeStatus.UNPLANNED,
        }[classification]

        if not payload.confirm:
            result = confirm_preview(
                {
                    "操作": "录入单笔成交并同步持仓",
                    "账户": f"{acc.account_id}（{acc.alias}）",
                    "成交": f"{action_v.value} {instrument_id}({side_v.value}) "
                    f"{qty_v} @ {price_v} {currency_v}",
                    "费用": f"fee {fee_v} / tax {tax_v} / other {other_v}",
                    "成交时间": executed_v.isoformat(),
                    "关联计划": related_plan_id or "用户主动成交",
                    "成交后现金": str(cash_after.total),
                    "核对结论": f"{match_status.value}：{match_reason}",
                    "成交后持仓条数": str(len(new_df)),
                }
            )
            result["holdings_after"] = df_records(new_df)
            return result

        try:
            trade = provisional.model_copy(update={"status": match_status})
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
               ws.portfolio_trades(acc.market,acc.instrument_class,acc.account_id)/f'{executed_v:%Y-%m}.parquet']
        trade_payload=dump_decimals(trade)
        with transaction(files):
            store.append_trades(acc.account_id,pd.DataFrame([trade_payload],columns=list(trade_payload)),market=acc.market,instrument_class=acc.instrument_class)
            store.write_holdings(acc.account_id,new_df,market=acc.market,instrument_class=acc.instrument_class)
            acc.cash=[c for c in acc.cash if c.currency!=currency_v]+[cash_after]
            AccountRegistry().update(acc)
        execution=None
        if related_plan_id:
            from portfolio.recommend import holdings_digest
            execution=ExecutionFeedbackService().record(trade,related_plan_id,
                holding_snapshot_ref=f'portfolio://{acc.account_id}/holdings/{holdings_digest(new_df)}')
        return json_safe({'trade_id':trade.trade_id,'holdings_rows':len(new_df),
            'execution_record_id':execution.execution_record_id if execution else None,
            'match_status':match_status.value,'cash_after':cash_after.total})

class TradesAppendPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    yaml_text: str

@router.post("/portfolio/trades/append")
def trades_append(payload: TradesAppendPayload) -> dict:
    from portfolio.transaction import lock
    with lock:
        import pandas as pd

        raw = _parse_yaml(payload.yaml_text, "成交列表")
        if not isinstance(raw, list) or not raw:
            raise PortfolioContractError(
                code="portfolio.trades_file_empty",
                message="成交内容必须为非空列表。",
                blocking=True,
            )
        df = pd.DataFrame(raw)
        if "account_id" not in df.columns:
            raise PortfolioContractError(
                code="portfolio.trades_missing_account",
                message="成交记录缺少 account_id 列。",
                blocking=True,
            )
        registry = AccountRegistry()
        groups = {str(k): len(g) for k, g in df.groupby("account_id")}
        for account_id in groups:
            registry.get(account_id)

        if not payload.confirm:
            return confirm_preview(
                {
                    "操作": "批量追加成交（不更新持仓）",
                    "总条数": str(len(df)),
                    "按账户": "、".join(f"{k}：{v} 条" for k, v in groups.items()),
                    "说明": "仅追加成交记录，用于导入 / 修复；持仓表不会同步更新。",
                }
            )

        store = PortfolioStore()
        total = 0
        detail = []
        for account_id, group in df.groupby("account_id"):
            account = registry.get(str(account_id))
            added = store.append_trades(
                account.account_id,
                group.reset_index(drop=True),
                market=account.market,
                instrument_class=account.instrument_class,
            )
            total += added
            detail.append({"account_id": account.account_id, "added": added})
        return {"total": total, "detail": detail}

class TradesFixPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    force: bool = False
    trade_id: str
    yaml_text: str

@router.post("/portfolio/trades/fix")
def trades_fix(payload: TradesFixPayload) -> dict:
    import pandas as pd

    raw = _parse_yaml(payload.yaml_text, "审计补丁")
    if not isinstance(raw, dict):
        raise PortfolioContractError(
            code="portfolio.trades_fix_payload_invalid",
            message="更正内容必须为一组字段和值。",
            blocking=True,
        )
    raw.setdefault("trade_id", payload.trade_id)
    if "account_id" not in raw:
        raise PortfolioContractError(
            code="portfolio.trades_fix_missing_account",
            message="审计补丁缺少 account_id 字段。",
            blocking=True,
        )
    account = AccountRegistry().get(raw["account_id"])

    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "写入成交审计补丁",
                "成交编号": payload.trade_id,
                "账户": account.account_id,
                "补丁字段": "、".join(sorted(raw.keys())),
                "说明": "审计行不修改原始成交文件；执行时须同时勾选 force。",
            }
        )
    if not payload.force:
        raise PortfolioContractError(
            code="portfolio.trades_fix_requires_force_confirm",
            message="请勾选“确认执行更正”，并确认提交。",
            blocking=True,
        )
    raw.setdefault("audited_at", datetime.now(UTC).isoformat())
    path = PortfolioStore().write_trade_audit(
        account.account_id,
        pd.DataFrame([raw]),
        market=account.market,
        instrument_class=account.instrument_class,
    )
    return json_safe({"trade_id": payload.trade_id, "path": str(path)})

# 对账

@router.get("/portfolio/{account_id}/reconcile")
def reconcile(account_id: str) -> dict:
    acc = AccountRegistry().get(account_id)
    store = PortfolioStore()

    since = last_reinit_at(acc)
    trades_df = store.read_trades(
        account_id, market=acc.market, instrument_class=acc.instrument_class, start=since
    )
    derived = replay_trades(trades_df)
    holdings = holdings_to_positions(
        store.read_holdings(account_id, market=acc.market, instrument_class=acc.instrument_class)
    )
    snap = store.latest_snapshot(
        account_id, market=acc.market, instrument_class=acc.instrument_class
    )
    snap_positions: dict[tuple[str, str], Decimal] = {}
    if snap is not None:
        for p in snap.positions:
            snap_positions[(p.instrument_id, p.side.value)] = p.qty

    rows = []
    mismatch = False
    warn = False
    for key in sorted(set(derived) | set(holdings) | set(snap_positions)):
        instrument_id, side_v = key
        d_qty = derived[key]["qty"] if key in derived else Decimal("0")
        h_qty = holdings[key]["qty"] if key in holdings else Decimal("0")
        s_qty = snap_positions.get(key, Decimal("0")) if snap is not None else None

        row_status = "ok"
        qty_targets = [h_qty] + ([s_qty] if s_qty is not None else [])
        if any(t != d_qty for t in qty_targets):
            row_status = "mismatch"
            mismatch = True
        elif (
            key in derived
            and key in holdings
            and derived[key]["cost_price"] != holdings[key]["cost_price"]
        ):
            row_status = "warn"
            warn = True
        rows.append(
            {
                "instrument_id": instrument_id,
                "side": side_v,
                "derived_qty": str(d_qty),
                "holdings_qty": str(h_qty),
                "snapshot_qty": str(s_qty) if s_qty is not None else "",
                "status": row_status,
            }
        )

    overall = "mismatch" if mismatch else "warn" if warn else "ok"
    return {
        "account_id": account_id,
        "status": overall,
        "has_snapshot": snap is not None,
        "since_reinit": since.isoformat() if since else "",
        "rows": rows,
    }

# 计划

class PlanSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    yaml_text: str
    primary_account: str

@router.post("/portfolio/plan/submit")
def plan_submit(payload: PlanSubmitPayload) -> dict:
    raw = _parse_yaml(payload.yaml_text, "调仓计划")
    if not isinstance(raw, dict):
        raise ContractViolation(code="WEBUI_INVALID_YAML", message="计划 YAML 顶层必须是映射。")
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_PLAN",
            message=f"计划字段校验失败：{exc.errors()[0].get('msg', '')}",
        ) from exc
    AccountRegistry().get(payload.primary_account)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "提交调仓计划",
                "计划 ID": plan.plan_id,
                "主账户": payload.primary_account,
                "状态": plan.state.value,
            }
        )
    path = PortfolioStore().write_plan(plan, primary_account=payload.primary_account)
    return json_safe({"plan_id": plan.plan_id, "path": str(path)})

class PlanTransitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    primary_account: str
    state: str

@router.post("/portfolio/plan/{plan_id}/transition")
def plan_transition(plan_id: str, payload: PlanTransitionPayload) -> dict:
    new_state = parse_enum(PlanState, payload.state, "state")
    AccountRegistry().get(payload.primary_account)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "切换计划状态",
                "计划 ID": plan_id,
                "主账户": payload.primary_account,
                "目标状态": new_state.value,
            }
        )
    path = PortfolioStore().transition_plan(
        plan_id, primary_account=payload.primary_account, new_state=new_state, confirm=True
    )
    return json_safe({"plan_id": plan_id, "state": new_state.value, "path": str(path)})

# 重新初始化

class ReinitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    reason: str = ""

@router.post("/portfolio/{account_id}/reinit")
def reinit_account(account_id: str, payload: ReinitPayload) -> dict:
    import pandas as pd

    registry = AccountRegistry()
    acc = registry.get(account_id)
    holdings = PortfolioStore().read_holdings(
        account_id, market=acc.market, instrument_class=acc.instrument_class
    )
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "重新初始化账户（破坏性）",
                "账户": f"{acc.account_id}（{acc.alias}）",
                "当前持仓条数": str(len(holdings)),
                "历史 reinit 次数": str(acc.reinit_count),
                "警示": "将清空当前持仓表并记录归因断点；成交与快照历史保留，但对账从断点重新开始。",
            }
        )
    if not payload.reason.strip():
        raise ContractViolation(
            code="WEBUI_REINIT_REASON_REQUIRED",
            message="重新初始化必须填写原因。",
        )
    store = PortfolioStore()
    store.write_holdings(
        acc.account_id,
        pd.DataFrame(columns=list(HOLDING_COLUMNS)),
        market=acc.market,
        instrument_class=acc.instrument_class,
    )
    store.append_reinit_log(
        acc.account_id,
        market=acc.market,
        instrument_class=acc.instrument_class,
        entry={
            "at": datetime.now(UTC).isoformat(),
            "reason": payload.reason.strip(),
            "previous_reinit_count": acc.reinit_count,
        },
    )
    updated = acc.model_copy(update={"reinit_count": acc.reinit_count + 1})
    registry.update(updated)
    return json_safe({"account_id": acc.account_id, "reinit_count": updated.reinit_count})
