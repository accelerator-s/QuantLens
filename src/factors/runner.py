from dataclasses import dataclass
from typing import Any

from ai.execution import run_factor
from ai.schemas import FactorCandidate, validate_ai_output

@dataclass
class CandidateRunReport:
    factor_name: str
    value: Any
    output_shape: str

def run_candidate(payload, *, panel_payload) -> CandidateRunReport:
    candidate = payload if isinstance(payload, FactorCandidate) else validate_ai_output(
        'factor_candidate', {**payload, 'task': 'factor_candidate'})
    value = run_factor(source=candidate.code, panel_payload=panel_payload)
    return CandidateRunReport(candidate.factor_name, value, f'{type(value).__name__}{value.shape}')
