from __future__ import annotations

from exceptions import QuantTrainingError

class MarketRuleError(QuantTrainingError):
    pass

class MarketRuleNotFoundError(MarketRuleError):
    pass

class MarketRuleValidationError(MarketRuleError):
    pass

class TradingSessionError(MarketRuleError):
    pass
