from __future__ import annotations

from exceptions import QuantTrainingError

class ProviderError(QuantTrainingError):
    pass

class ProviderNetworkError(ProviderError):
    pass

class ProviderAuthError(ProviderError):
    pass

class ProviderVerificationBlockedError(ProviderAuthError):
    pass

class ProviderDataError(ProviderError):
    pass

class ProviderContractError(ProviderError):
    pass
