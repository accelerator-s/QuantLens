from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Protocol, runtime_checkable

from .models import MarketRuleConfig, SessionType

# 运行时交易时段

@dataclass(frozen=True)
class MarketSession:

    name: str
    start: time
    end: time
    session_type: SessionType

    def contains_time(self, t: time) -> bool:
        
        return self.start <= t <= self.end

def build_sessions(config: MarketRuleConfig) -> list[MarketSession]:
    
    return [
        MarketSession(
            name=s.name,
            start=s.start,
            end=s.end,
            session_type=s.session_type,
        )
        for s in config.sessions
    ]

@runtime_checkable
class TradingCalendar(Protocol):

    def is_trading_day(self, d: date) -> bool:
        
        ...

    def next_trading_day(self, d: date) -> date:
        
        ...

    def prev_trading_day(self, d: date) -> date:
        
        ...

    def trading_days_between(self, start: date, end: date) -> list[date]:
        
        ...

# 通用日历实现

class SimpleTradingCalendar:

    def __init__(self, holidays: set[date] | None = None) -> None:
        self._holidays: set[date] = holidays or set()

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays

    def next_trading_day(self, d: date) -> date:
        nxt = d + timedelta(days=1)
        while not self.is_trading_day(nxt):
            nxt += timedelta(days=1)
        return nxt

    def prev_trading_day(self, d: date) -> date:
        prev = d - timedelta(days=1)
        while not self.is_trading_day(prev):
            prev -= timedelta(days=1)
        return prev

    def trading_days_between(self, start: date, end: date) -> list[date]:
        days: list[date] = []
        cur = start
        while cur <= end:
            if self.is_trading_day(cur):
                days.append(cur)
            cur += timedelta(days=1)
        return days
