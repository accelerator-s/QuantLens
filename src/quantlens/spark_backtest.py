from quantlens.warehouse import job, lineage, publish


def attribution(nav, positions, factor_returns, weights):
    """Geometrically link contributions so both daily and cumulative sums reconcile."""
    import pandas as pd
    rows=[];cumulative={};growth=1.0
    for date,ret in zip(nav.date,nav.daily_return):
        daily_weights=weights.get(date,weights)
        contribution={name:float(weight)*float(factor_returns.get((date,name),0)) for name,weight in daily_weights.items()}
        contribution['residual']=float(ret)-sum(contribution.values())
        for name,value in contribution.items():
            cumulative[name]=cumulative.get(name,0)+growth*value
            rows.append({'date':date,'factor':name,'contribution':value,'cumulative_contribution':cumulative[name],'portfolio_return':float(ret)})
        growth*=1+float(ret)
    return pd.DataFrame(rows)


def run(spark,context,profile_name,start,end,capital=1000000,run_id=None,profile_payload=None,protocol_payload=None,config_payload=None):
    import json
    import pandas as pd
    from pyspark.sql import functions as F
    from backtest.config import BacktestConfig,resolve_and_check
    from backtest.protocol import verify_protocol_or_raise
    from backtest.engine import run_backtest
    from backtest.report import BacktestRunStore
    from quantlens.backtest_inputs import inputs
    from backtest.protocol import BacktestProtocol
    from strategy import ResolvedStrategyProfile
    protocol=BacktestProtocol.model_validate(protocol_payload) if protocol_payload else verify_protocol_or_raise()
    if not protocol.lock.locked or protocol.lock.protocol_hash!=protocol.content_hash():raise ValueError('Backtest protocol lock mismatch')
    config=BacktestConfig.model_validate(config_payload) if config_payload else BacktestConfig(profile_name=profile_name,start=start,end=end,initial_capital=capital)
    if (config.profile_name,config.start,config.end)!=(profile_name,start,end):raise ValueError('Backtest config arguments mismatch')
    profile=ResolvedStrategyProfile.model_validate(profile_payload) if profile_payload else resolve_and_check(config,protocol)
    from backtest.config import _profile_protocol_diffs
    if _profile_protocol_diffs(profile,protocol):raise ValueError('Profile conflicts with frozen protocol')
    print(f'QUANTLENS_BACKTEST=读取回测数据 {start} 至 {end}', flush=True)
    payload=inputs(profile,start,end,spark)
    print('QUANTLENS_BACKTEST=执行逐日撮合', flush=True)
    outcome=run_backtest(config,protocol=protocol,profile=profile,warehouse_inputs=payload,run_id=run_id)
    print(f'QUANTLENS_BACKTEST=回测完成 {outcome.run_id}，保存归因与结果', flush=True)
    store=BacktestRunStore.open_run(outcome.run_id)
    nav=store.read_daily('nav');positions=store.read_daily('positions')
    factor_returns={};weights={}
    fitted=[]
    strategy_id=profile.extra.get('weight_strategy_id',profile.profile_name)
    if profile.factors.weighting.value!='equal' and any(ref.weight_override is None for ref in profile.factors.items):
        fitted=spark.table('dws.factor_weights').where((F.col('strategy_id')==strategy_id)&(F.col('method')==profile.factors.weighting.value)&(F.col('train_end')<F.col('as_of'))).orderBy('as_of').collect()
    from factors.registry import FactorRegistry
    versions={ref.name:profile.extra.get('factor_versions',{}).get(ref.name,FactorRegistry().get(ref.name).version) for ref in profile.factors.items}
    trading_dates=payload['market']['ret_wd'].index.sort_values()
    signal_dates=pd.Series(trading_dates,index=trading_dates).shift(2)
    for date in pd.to_datetime(nav.date):
        signal_date=signal_dates.get(date,pd.NaT)
        daily={}
        for ref in profile.factors.items:
            candidates=[row for row in fitted if row.factor==ref.name and row.factor_version==versions[ref.name] and pd.Timestamp(row.as_of)<=signal_date]
            daily[ref.name]=float(ref.weight_override if ref.weight_override is not None else (1/len(profile.factors.items) if profile.factors.weighting.value=='equal' else candidates[-1].weight if candidates else 0))
        mass=sum(abs(value) for value in daily.values())
        weights[date]={name:value/mass if mass else 0 for name,value in daily.items()}
    # Realized lagged factor-mimicking portfolio return; no forward labels in attribution.
    from factors.registry import FactorRegistry
    for ref in profile.factors.items:
        meta=FactorRegistry().get(ref.name)
        values=spark.table('dws.factor_values').where((F.col('factor')==ref.name)&(F.col('version')==versions[ref.name])&(F.col('date')>=str(trading_dates.min())[:10])&(F.col('date')<=end)).select('code','date','std_value').toPandas()
        values.date=pd.to_datetime(values.date)
        from backtest.signal import _resolve_direction
        score=values.pivot(index='date',columns='code',values='std_value').sort_index()*_resolve_direction(ref.name,ref.direction_override,meta.direction)
        score=score.reindex(trading_dates)
        members=score.rank(axis=1,pct=True).ge(.8).shift(2).fillna(False)
        returns=payload['market']['ret_wd'].reindex(index=score.index,columns=score.columns).where(members).mean(axis=1).fillna(0)
        factor_returns.update({(dt,ref.name):float(value) for dt,value in returns.items()})
    attr=attribution(nav,positions,factor_returns,weights)
    source_id=profile.extra.get('source_strategy_id',profile_name)
    def save(table,frame):
        frame=lineage(frame,context)
        if spark.catalog.tableExists('ads.'+table):
            old=spark.table('ads.'+table).where((F.date_format('as_of','yyyy-MM-dd')==end)&(F.col('backtest_run')!=outcome.run_id))
            frame=old.unionByName(frame)
        publish(spark,frame,'ads.'+table,['as_of'],run=context)
    for table,pdf in [('backtest_nav',nav),('attribution',attr),('backtest_trades',store.read_daily('trades')),('backtest_positions',positions)]:
        if pdf.empty:continue
        if table=='backtest_trades':
            schema='date string, instrument string, side string, status string, target_weight double, shares long, anchor_price double, exec_price double, amount double, commission double, transfer_fee double, stamp_tax double, other_fees double, slippage_cost double, note string'
            pdf=pdf.copy();pdf['date']=pdf.date.astype(str)
            frame=spark.createDataFrame(pdf,schema=schema).withColumn('date',F.to_date('date'))
        else:frame=spark.createDataFrame(pdf).withColumn('date',F.to_date('date'))
        frame=frame.withColumn('backtest_run',F.lit(outcome.run_id)).withColumn('strategy_id',F.lit(source_id)).withColumn('as_of',F.to_date(F.lit(end)))
        save(table,frame)
    summary=spark.createDataFrame([(outcome.run_id,source_id,json.dumps(outcome.metrics,default=str),protocol.content_hash(),context['data_snapshot'],json.dumps(store.read_config(),default=str),json.dumps(store.read_status(),default=str))], 'backtest_run string, strategy_id string, metrics string, protocol_hash string, input_snapshot string, config string, status string').withColumn('as_of',F.to_date(F.lit(end)))
    save('backtest_summary',summary)
    return outcome

def run_windows(spark, context, windows, profile_payload, protocol_payload, *, deadline=None, control_path=None, batch_id=None):
    """Share one Spark session while keeping each window's portfolio independent."""
    import json
    import time
    from pathlib import Path

    outcomes = []
    for index, item in enumerate(windows):
        if deadline is not None and time.time() >= deadline:
            print('QUANTLENS_WINDOWS_STOPPED=timeout', flush=True)
            break
        if control_path and json.loads(Path(control_path).read_text()).get('cancel_requested'):
            print('QUANTLENS_WINDOWS_STOPPED=cancelled', flush=True)
            break
        print('QUANTLENS_WINDOW_STARTED=' + json.dumps({'window': item['window']}), flush=True)
        config = item['config']
        outcome = run(spark, context, config['profile_name'], config['start'], config['end'],
                      config['initial_capital'], item['run_id'], profile_payload, protocol_payload, config)
        outcomes.append(outcome)
        completed = {'window': item['window'], 'run_id': outcome.run_id}
        if batch_id:
            from quantlens.spark_ads import export_backtests
            completed['batch'] = f'{batch_id}-{index:02d}'
            export_backtests(spark, context, completed['batch'], config['end'])
        print('QUANTLENS_WINDOW_COMPLETED=' + json.dumps(completed), flush=True)
        if not outcome.metrics.get('input_quality', {}).get('passed', False):
            print('QUANTLENS_WINDOWS_STOPPED=input_quality', flush=True)
            break
    return outcomes


if __name__=='__main__':
    import argparse
    import json
    parser=argparse.ArgumentParser();parser.add_argument('--strategy');parser.add_argument('--start');parser.add_argument('--end');parser.add_argument('--capital',type=float,default=1000000);parser.add_argument('--run-id');parser.add_argument('--batch');parser.add_argument('--profile');parser.add_argument('--protocol')
    parser.add_argument('--windows');parser.add_argument('--deadline',type=float);parser.add_argument('--research-id')
    parser.add_argument('--config');args=parser.parse_args()
    with job('backtest') as (spark,context):
        if args.windows:
            import re
            from paths import workspace
            windows = json.loads(args.windows)
            if not windows or len(windows) > 40:
                raise ValueError('Invalid research windows')
            if not args.batch:
                parser.error('--batch is required for research windows')
            if args.research_id and not re.fullmatch(r'EVO-[A-Za-z0-9-]+', args.research_id):
                raise ValueError('Invalid research ID')
            control = workspace().runs / 'factor_evolution' / args.research_id / 'status.json' if args.research_id else None
            outcomes = run_windows(spark, context, windows, json.loads(args.profile), json.loads(args.protocol),
                                   deadline=args.deadline, control_path=control, batch_id=args.batch)
            export_end = max(item['config']['end'] for item in windows)
        else:
            if not all((args.strategy, args.start, args.end)):
                parser.error('--strategy, --start and --end are required for a single window')
            outcomes = [run(spark,context,args.strategy,args.start,args.end,args.capital,args.run_id,json.loads(args.profile) if args.profile else None,json.loads(args.protocol) if args.protocol else None,json.loads(args.config) if args.config else None)]
            export_end = args.end
        if args.batch and outcomes and not args.windows:
            from quantlens.spark_ads import export_backtests
            print('QUANTLENS_BACKTEST=导出回测结果', flush=True)
            export_backtests(spark,context,args.batch,export_end)
