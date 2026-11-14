"""Execute one frozen strategy-training stage; the host owns the AI dialogue."""
from __future__ import annotations


def run(spark, context, profile, spec, protocol, config, run_id, batch, stage, candidate):
    from strategy.evaluation import evaluate
    from strategy.research import write_result
    from strategy.account_binding import require_binding
    require_binding(profile, spec.account_id)
    snapshot = profile.extra['research_account_snapshot']
    if str(config.initial_capital) != snapshot['capital'] or config.account_id != spec.account_id:
        raise ValueError('研究资金或账户与冻结快照不一致')
    outcome = evaluate(spark, context, profile, spec, protocol, config, candidate, stage,
                       run_id=run_id, batch=batch)
    result = {'state': 'computed', 'run_id': run_id, 'stage': stage, 'evaluation': outcome}
    write_result(profile.profile_name, result)
    return result


if __name__ == '__main__':
    import argparse
    from strategy import ResolvedStrategyProfile
    from strategy.research import ResearchSpec
    from strategy.training import Candidate
    from backtest.config import BacktestConfig
    from backtest.protocol import BacktestProtocol
    from quantlens.warehouse import job
    parser = argparse.ArgumentParser()
    for key in ['profile', 'spec', 'protocol', 'config', 'run-id', 'batch', 'candidate']:
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--stage', choices=['inner', 'final'], required=True)
    args = parser.parse_args()
    with job('strategy_research') as (spark, context):
        run(spark, context, ResolvedStrategyProfile.model_validate_json(args.profile),
            ResearchSpec.model_validate_json(args.spec), BacktestProtocol.model_validate_json(args.protocol),
            BacktestConfig.model_validate_json(args.config), args.run_id, args.batch, args.stage,
            Candidate.model_validate_json(args.candidate))
