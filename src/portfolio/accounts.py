from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml

from exceptions import (
    PortfolioAccountAmbiguousError,
    PortfolioAccountNotFoundError,
    PortfolioContractError,
)
from paths import Workspace, workspace

from .models import Account, Cash

DEFAULT_ACCOUNT_ID = "research-default"
DEFAULT_ACCOUNT_ALIAS = "通用研究（包含科创板）"

class AccountRegistry:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    def ensure_default(self) -> Account | None:
        from .transaction import lock
        with lock:
            if DEFAULT_ACCOUNT_ID in self._read_index():
                return self.get(DEFAULT_ACCOUNT_ID)
            if self._read_index_data().get("default_account_deleted"):
                return None
            path = self.ws.portfolio_account('CN-A', 'stock', DEFAULT_ACCOUNT_ID) / 'account.yaml'
            if path.exists():
                # Preserve account balances if only the index write was interrupted.
                account = Account.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))
            else:
                capital = Decimal('1000000')
                account = Account(account_id=DEFAULT_ACCOUNT_ID, alias=DEFAULT_ACCOUNT_ALIAS,
                                  market='CN-A', instrument_class='stock', reporting_currency='CNY',
                                  star_market_enabled=True, created_at=datetime.now(UTC),
                                  cash=[Cash(currency='CNY', total=capital, available=capital,
                                             withdrawable=capital)], notes='研究模拟账户。')
            self.create(account)
            return account

    # 索引

    def _read_index_data(self) -> dict:
        idx = self.ws.portfolio_index
        if not idx.exists():
            return {}
        return yaml.safe_load(idx.read_text(encoding="utf-8")) or {}

    def _read_index(self) -> dict[str, dict[str, str]]:
        return dict(self._read_index_data().get("accounts") or {})

    def _write_index(self, accounts: dict[str, dict[str, str]], *, default_account_deleted: bool | None = None) -> None:
        idx = self.ws.portfolio_index
        idx.parent.mkdir(parents=True, exist_ok=True)
        data = self._read_index_data()
        data["accounts"] = accounts
        if default_account_deleted is not None:
            data["default_account_deleted"] = default_account_deleted
        tmp = idx.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(
                data,
                allow_unicode=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(idx)

    def _account_path(self, account: Account) -> Path:
        return (
            self.ws.portfolio_account(account.market, account.instrument_class, account.account_id)
            / "account.yaml"
        )

    def _locate(self, account_id: str) -> tuple[str, str]:
        
        index = self._read_index()
        entry = index.get(account_id)
        if not entry:
            raise PortfolioAccountNotFoundError(
                code="portfolio.account_not_found",
                message=f"未找到账户：{account_id}",
                context={"account_id": account_id},
                blocking=True,
                suggested_action="使用 `account list` 查看已注册账户，或 `account create` 新建",
            )
        return entry["market"], entry["instrument_class"]

    def list(
        self,
        *,
        market: str | None = None,
        instrument_class: str | None = None,
    ) -> list[Account]:
        
        index = self._read_index()
        out: list[Account] = []
        for account_id, entry in sorted(index.items(), key=lambda item: (item[0] != DEFAULT_ACCOUNT_ID, item[0])):
            if market is not None and entry.get("market") != market:
                continue
            if instrument_class is not None and entry.get("instrument_class") != instrument_class:
                continue
            out.append(self.get(account_id))
        return out

    def get(self, account_id: str) -> Account:
        
        market, instrument_class = self._locate(account_id)
        path = self.ws.portfolio_account(market, instrument_class, account_id) / "account.yaml"
        if not path.exists():
            raise PortfolioContractError(
                code="portfolio.account_file_missing",
                message=f"索引存在但文件缺失：{path}",
                context={"account_id": account_id},
                blocking=True,
                suggested_action="请检查工作区是否手动删除过账户目录；可重新创建账户",
            )
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Account.model_validate(raw)

    def resolve(self, account_id: str = "") -> Account:
        if account_id:
            return self.get(account_id)
        accounts = self.list()
        if accounts:
            return accounts[0]
        raise PortfolioAccountNotFoundError(
            code="portfolio.account_not_found",
            message="尚未添加账户。",
            blocking=True,
            suggested_action="请先在账户持仓中创建账户。",
        )

    def create(self, account: Account) -> None:
        
        index = self._read_index()
        if account.account_id in index:
            raise PortfolioContractError(
                code="portfolio.account_id_taken",
                message=f"account_id 已存在：{account.account_id}",
                context={"account_id": account.account_id},
                blocking=True,
                suggested_action="请使用其他账户编号，或删除原账户。",
            )

        if not account.created_at.tzinfo:
            account = account.model_copy(
                update={"created_at": account.created_at.replace(tzinfo=UTC)}
            )

        target = self._account_path(account)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._dump_account(account, target)

        index[account.account_id] = {
            "market": account.market,
            "instrument_class": account.instrument_class,
            "alias": account.alias,
            "broker": account.broker,
        }
        self._write_index(index)

    def update(self, account: Account) -> None:
        
        # 校验位置一致
        market, instrument_class = self._locate(account.account_id)
        if market != account.market or instrument_class != account.instrument_class:
            raise PortfolioContractError(
                code="portfolio.account_relocation_forbidden",
                message=(
                    f"账户 {account.account_id} 不允许跨 (market, instrument_class) 更新："
                    f"{(market, instrument_class)} -> "
                    f"{(account.market, account.instrument_class)}"
                ),
                blocking=True,
                suggested_action="请创建新账户。",
            )

        self._dump_account(account, self._account_path(account))

        index = self._read_index()
        index[account.account_id] = {
            "market": account.market,
            "instrument_class": account.instrument_class,
            "alias": account.alias,
            "broker": account.broker,
        }
        self._write_index(index)
        from strategy.capital_guard import on_mutation
        on_mutation(account.account_id)

    def delete(self, account_id: str, *, confirm: bool, deleted_by: str = "user") -> None:
        from .transaction import lock
        with lock:
            if not confirm:
                raise PortfolioContractError(
                    code="portfolio.delete_requires_confirm",
                    message="删除账户必须显式 confirm=True。",
                    blocking=True,
                )
            account = self.get(account_id)
            from strategy import StrategyProfileStore
            bound = [d.profile_name for d in StrategyProfileStore(self.ws.config / 'strategies').list()
                     if d.extra.get('account_binding', {}).get('account_id') == account_id]
            if bound:
                raise PortfolioContractError(code='portfolio.account_has_strategies',
                    message='该账户仍有关联策略，请先删除这些策略后再删除账户。',
                    context={'strategies': bound}, blocking=True)
            from .deletion import delete_account_data
            delete_account_data(self.ws, account)
            index = self._read_index()
            index.pop(account_id)
            self._write_index(index, default_account_deleted=True if account_id == DEFAULT_ACCOUNT_ID else None)

    def choose(
        self,
        *,
        market: str,
        instrument_class: str,
        mode: Literal["single", "subset", "all"],
        pre_selected: list[str] | None = None,
    ) -> list[str]:
        
        candidates = [
            a.account_id for a in self.list(market=market, instrument_class=instrument_class)
        ]
        if not candidates:
            raise PortfolioAccountNotFoundError(
                code="portfolio.no_account_for_scope",
                message=(
                    f"(market={market!r}, instrument_class={instrument_class!r}) 下没有任何账户"
                ),
                blocking=True,
                suggested_action="先 `account create` 创建至少一个账户",
            )

        if mode == "all":
            return sorted(candidates)
        if mode == "single":
            if len(candidates) > 1:
                raise PortfolioAccountAmbiguousError(
                    code="portfolio.account_ambiguous",
                    message=(
                        f"(market={market!r}, instrument_class={instrument_class!r}) "
                        f"下存在 {len(candidates)} 个账户，需明确指定"
                    ),
                    context={"candidates": candidates},
                    blocking=True,
                    suggested_action=(
                        "在调用方通过 Wizard 让用户选择，或传入 mode='subset' "
                        "+ pre_selected 指定要使用的账户"
                    ),
                )
            return candidates
        if mode == "subset":
            chosen = [a for a in (pre_selected or []) if a in candidates]
            if not chosen:
                raise PortfolioContractError(
                    code="portfolio.subset_empty",
                    message="subset 选择结果为空",
                    context={"candidates": candidates, "pre_selected": pre_selected or []},
                    blocking=True,
                    suggested_action="请重新让用户选择至少 1 个账户",
                )
            return chosen
        raise PortfolioContractError(
            code="portfolio.choose_mode_invalid",
            message=f"非法 choose mode：{mode!r}",
            blocking=True,
            suggested_action="mode 必须为 'single' / 'subset' / 'all' 之一",
        )

    # 内部工具

    @staticmethod
    def _dump_account(account: Account, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = account.model_dump(mode="json")
        
        for cash in payload.get("cash", []):
            for k in ("total", "available", "withdrawable", "frozen"):
                if k in cash and cash[k] is not None:
                    cash[k] = str(cash[k])
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(target)

def utcnow() -> datetime:
    
    return datetime.now(tz=UTC)

__all__ = ["AccountRegistry", "utcnow"]
