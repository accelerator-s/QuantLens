import uuid,json
from backtest.engine import BacktestOutcome
from backtest.report import BacktestRunStore
from quantlens.compute import submit
from quantlens.sync_ads import sync


def run_remote(config,profile,protocol,run_id=None):
    run_id=run_id or 'BT-'+uuid.uuid4().hex
    batch_id='backtest-'+uuid.uuid4().hex
    submit('spark_backtest','--strategy',profile.profile_name,'--start',config.start,'--end',config.end,
           '--profile',json.dumps(profile.model_dump(mode='json')),'--protocol',json.dumps(protocol.model_dump(mode='json')),
           '--capital',str(config.initial_capital),'--run-id',run_id,'--batch',batch_id,
           '--config',json.dumps(config.model_dump(mode='json')))
    sync(batch_id)
    record=BacktestRunStore.open_run(run_id)
    return BacktestOutcome(run_id,record.run_dir,record.read_metrics())
