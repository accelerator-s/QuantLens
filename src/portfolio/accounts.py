from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml

from exceptions import (
    PortfolioAccountAmbiguousError,
    PortfolioAccountNotFoundError,
    PortfolioContractError,
)
from paths import Workspace, workspace
from trash import TrashStore

from .models import Account

class AccountRegistry:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    # 索引

    def _read_index(self) -> dict[str, dict[str, str]]:
        idx = self.ws.portfolio_index
        if not idx.exists():
            return {}
        raw = yaml.safe_load(idx.read_text(encoding="utf-8")) or {}
        return dict(raw.get("accounts") or {})

    def _write_index(self, accounts: dict[str, dict[str, str]]) -> None:
        idx = self.ws.portfolio_index
        idx.parent.mkdir(parents=True, exist_ok=True)
        tmp = idx.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(
                {"accounts": accounts},
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
        for account_id, entry in sorted(index.items()):
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

    def create(self, account: Account) -> None:
        
        index = self._read_index()
        if account.account_id in index:
            raise PortfolioContractError(
                code="portfolio.account_id_taken",
                message=f"account_id 已存在：{account.account_id}",
                context={"account_id": account.account_id},
                blocking=True,
                suggested_action="改用其它 account_id，或先 `account archive` 旧账户",
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
                suggested_action="如确需迁移，请先 archive 旧账户再以新 account_id 创建",
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

    def archive(self, account_id: str, *, confirm: bool, deleted_by: str = "user") -> None:
        
        if not confirm:
            raise PortfolioContractError(
                code="portfolio.archive_requires_confirm",
                message="归档账户为破坏性操作，必须显式 confirm=True",
                blocking=True,
                suggested_action="CLI 加 `--confirm`，编程接口传 confirm=True",
            )
        market, instrument_class = self._locate(account_id)
        account_dir = self.ws.portfolio_account(market, instrument_class, account_id)
        if account_dir.exists():
            TrashStore(self.ws).move(
                account_dir,
                category="portfolio_account",
                deleted_by=deleted_by,
                reason=f"archive account {account_id}",
            )
        index = self._read_index()
        index.pop(account_id, None)
        self._write_index(index)

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
