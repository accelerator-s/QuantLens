from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass
class QuantTrainingError(Exception):

    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    impact: str = ""
    blocking: bool = False
    suggested_action: str = ""
    related_files: list[str] = field(default_factory=list)
    related_batches: list[str] = field(default_factory=list)
    related_subjects: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(f"[{self.code}] {self.message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "context": self.context,
            "impact": self.impact,
            "blocking": self.blocking,
            "suggested_action": self.suggested_action,
            "related_files": self.related_files,
            "related_batches": self.related_batches,
            "related_subjects": self.related_subjects,
        }

class WorkspaceError(QuantTrainingError):
    pass

class WizardError(QuantTrainingError):
    pass

class ModelRouteError(QuantTrainingError):
    pass

class FactorExecutionError(QuantTrainingError):
    pass

class ContractViolation(QuantTrainingError):
    pass

class PanelCacheError(QuantTrainingError):
    pass

class SolveError(QuantTrainingError):
    pass

class StrategyProfileError(QuantTrainingError):
    pass

class BacktestError(QuantTrainingError):
    pass

class ExperimentError(QuantTrainingError):
    pass

class LifecycleError(QuantTrainingError):
    pass

class PortfolioError(QuantTrainingError):
    pass

class PortfolioContractError(PortfolioError):
    pass

class PortfolioAdapterMissingError(PortfolioError):
    pass

class PortfolioReconcileError(PortfolioError):
    pass

class PortfolioPlanStaleError(PortfolioError):
    pass

class PortfolioInsufficientError(PortfolioError):
    pass

class PortfolioAccountNotFoundError(PortfolioError):
    pass

class PortfolioAccountAmbiguousError(PortfolioError):
    pass

class FxError(QuantTrainingError):
    pass

class FxRateUnavailableError(FxError):
    pass

class FxStaleRateError(FxError):
    pass

class FxContractError(FxError):
    pass
