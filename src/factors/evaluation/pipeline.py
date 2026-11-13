from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from exceptions import ContractViolation
from factors.registry import FactorRegistry
from .config import EvaluationConfig
from .report import RunStore


@dataclass
class EvaluationOutcome:
    run_id:str
    run_dir:Path
    metrics:dict


def evaluate_factor(config:EvaluationConfig,*,resume_run_id=None,on_progress:Callable|None=None):
    """Submit the canonical DWS evaluator and retain its serving result as evidence."""
    from quantlens.compute import submit
    from quantlens.sync_ads import sync
    from quantlens.warehouse import make_run
    from quantlens import ads
    if config.return_field!='ret_wd':
        raise ContractViolation(code='LABEL_CONTRACT_MISMATCH',message='评价统一使用 dwd.label_forward 的复权标签',suggested_action='使用 ret_wd；标签口径变更须新建 label_version')
    FactorRegistry().get(config.factor_name)
    record=RunStore.open_resume(config,resume_run_id) if resume_run_id else RunStore.create(config)
    batch=make_run('evaluation')['run_id']
    if on_progress:on_progress('读取 DWS 因子、DWD 标签和 universe，提交 Spark 统一评价')
    try:
        submit('spark_factor_eval','--factor',config.factor_name,'--start',config.start,'--end',config.end,'--batch',batch)
        publication=sync(batch)
        version=FactorRegistry().get(config.factor_name).version
        rows=ads.query('factor_eval','factor=? AND version=? AND run_id=? AND eval_start=CAST(? AS DATE) AND eval_end=CAST(? AS DATE)',[config.factor_name,version,publication['run_id'],config.start,config.end],limit=100)
        primary=next((row for row in rows if row['horizon']==config.primary_horizon),None)
        if primary is None:raise ContractViolation(code='EVALUATION_EMPTY',message='该窗口无成熟标签和因子交集',suggested_action='核对已发布分区、标签版本及持有期')
        from quantlens.evaluation_view import view
        daily=ads.query('factor_eval_daily','factor=? AND version=? AND run_id=? AND horizon=?',[config.factor_name,primary['version'],primary['run_id'],config.primary_horizon],limit=10000,order='date')
        metrics={**view(rows,daily,config.primary_horizon,FactorRegistry().get(config.factor_name).direction),**ads.provenance('factor_eval')}
        record.write_metrics(metrics);record.update_status(state='completed')
        return EvaluationOutcome(record.run_id,record.run_dir,metrics)
    except Exception:
        record.update_status(state='failed');raise

__all__=['EvaluationOutcome','evaluate_factor']
