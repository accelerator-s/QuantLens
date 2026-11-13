from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "QUANTLENS_HOME"
_ROOT_MARKERS = ("main.py", "src")

def market_slug(market: str) -> str:
    
    return market.lower().replace("-", "_")

def _detect_project_root(start: Path) -> Path:
    
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        if all((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return start.resolve()

def get_workspace_root() -> Path:
    
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).expanduser().resolve()
    return _detect_project_root(Path(__file__).parent)

@dataclass(frozen=True)
class Workspace:

    root: Path

    # 数据层
    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def data_raw(self) -> Path:
        return self.data / "raw"

    @property
    def data_staged(self) -> Path:
        return self.data / "staged"

    @property
    def data_manifests(self) -> Path:
        return self.data / "manifests"

    # 治理 / 评审
    @property
    def proposals(self) -> Path:
        return self.root / "proposals"

    @property
    def trash(self) -> Path:
        return self.root / "trash"

    # 经验 / 状态
    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def chat_history(self) -> Path:
        return self.data / "chat_history"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def runs_backtest(self) -> Path:
        return Path(os.environ["QUANTLENS_BACKTEST_ROOT"]) if os.environ.get("QUANTLENS_BACKTEST_ROOT") else self.data / "backtest_runs"

    @property
    def experience(self) -> Path:
        return self.root / "experience"

    @property
    def factor_experiments_db(self) -> Path:
        
        return self.experience / "factor_experiments.sqlite"

    @property
    def lifecycle_db(self) -> Path:
        
        return self.experience / "research_lifecycle.sqlite"

    @property
    def webui_tasks_db(self) -> Path:
        
        return self.experience / "webui_tasks.sqlite"

    # 配置
    @property
    def config(self) -> Path:
        return self.root / "config"

    # 市场规则
    @property
    def market_rules(self) -> Path:
        return self.config / "market_rules"

    # 策略配置
    @property
    def strategies(self) -> Path:
        return self.config / "strategies"

    @property
    def portfolio_root(self) -> Path:
        return self.root / "portfolio"

    @property
    def portfolio_index(self) -> Path:
        return self.portfolio_root / "_index.yaml"

    def portfolio_market(self, market: str) -> Path:
        return self.portfolio_root / market_slug(market)

    def portfolio_class(self, market: str, instrument_class: str) -> Path:
        return self.portfolio_market(market) / instrument_class

    def portfolio_account(self, market: str, instrument_class: str, account_id: str) -> Path:
        return self.portfolio_class(market, instrument_class) / account_id

    def portfolio_holdings(self, market: str, instrument_class: str, account_id: str) -> Path:
        return self.portfolio_account(market, instrument_class, account_id) / "holdings"

    def portfolio_trades(self, market: str, instrument_class: str, account_id: str) -> Path:
        return self.portfolio_account(market, instrument_class, account_id) / "trades"

    def portfolio_trades_audit(self, market: str, instrument_class: str, account_id: str) -> Path:
        return self.portfolio_account(market, instrument_class, account_id) / "trades_audit"

    def portfolio_snapshots(self, market: str, instrument_class: str, account_id: str) -> Path:
        return self.portfolio_account(market, instrument_class, account_id) / "snapshots"

    def portfolio_plans(self, market: str, instrument_class: str, account_id: str) -> Path:
        return self.portfolio_account(market, instrument_class, account_id) / "plans"

    def staged_for(self, market: str, instrument_class: str) -> Path:
        
        return self.data_staged / market_slug(market) / instrument_class

    def raw_for(self, market: str, instrument_class: str, provider: str) -> Path:
        
        return self.data_raw / market_slug(market) / instrument_class / provider

    def manifest_for(self, market: str, instrument_class: str, provider: str) -> Path:
        
        return self.data_manifests / market_slug(market) / instrument_class / provider

    def all_dirs(self) -> list[Path]:
        
        return [
            self.data_raw,
            self.data_staged,
            self.data_manifests,
            self.proposals,
            self.trash,
            self.memory,
            self.chat_history,
            self.memory / "candidates",
            self.memory / "long_term",
            self.runs,
            self.runs_backtest,
            self.experience,
            self.config,
            self.market_rules,
            self.strategies,
            self.root / "factors",
            self.root / "skills",
            self.portfolio_root,
        ]

def workspace() -> Workspace:
    
    return Workspace(root=get_workspace_root())
