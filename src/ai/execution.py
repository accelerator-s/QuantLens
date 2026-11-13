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
    namespace = {'__name__': 'quantlens_factor'}
    try:
        exec(compile(source, '<factor>', 'exec'), namespace)
        compute = namespace.get('compute')
        if not callable(compute):
            raise ContractViolation(code='FACTOR_ENTRY_MISSING', message='因子缺少 compute(panel) 入口',
                                    suggested_action='在因子代码中定义 compute 函数')
        value = compute(panel_payload)
        if not isinstance(value, pd.DataFrame):
            raise ContractViolation(code='FACTOR_OUTPUT_INVALID', message='因子应返回日期 × 证券的 DataFrame',
                                    suggested_action='检查 compute 函数返回值')
        return value
    except QuantTrainingError:
        raise
    except Exception as exc:
        raise FactorExecutionError(code='FACTOR_EXECUTION_FAILED', message=f'{type(exc).__name__}: {exc}',
                                   suggested_action='在因子库检查代码和依赖') from exc
