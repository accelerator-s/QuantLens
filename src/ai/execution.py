from typing import Any

import pandas as pd

from exceptions import ContractViolation, FactorExecutionError, QuantTrainingError

def syntax_check(source: str) -> list[str]:
    try:
        compile(source, '<factor>', 'exec')
    except SyntaxError as exc:
        return [str(exc)]
    return []

def run_factor(*, source: str, panel_payload: dict[str, Any]):
    from factors.sandbox import execute_factor
    try:
        value = execute_factor(source, panel_payload)
        if not isinstance(value, pd.DataFrame):
            raise ContractViolation(code='FACTOR_OUTPUT_INVALID', message='因子应返回日期 × 证券的 DataFrame',
                                    suggested_action='检查 compute 函数返回值')
        return value
    except QuantTrainingError:
        raise
    except Exception as exc:
        raise FactorExecutionError(code='FACTOR_EXECUTION_FAILED', message=f'{type(exc).__name__}: {exc}',
                                   suggested_action='在因子库检查代码和依赖') from exc
