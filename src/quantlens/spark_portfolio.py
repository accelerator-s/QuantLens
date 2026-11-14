from quantlens.warehouse import job, lineage, publish


def run(spark,context,strategy_id,as_of=None,profile_payload=None,
        star_market_enabled=True,capital=None,target_id=None):
    from pyspark.sql import functions as F, Window
    from strategy import StrategyProfileStore,ResolvedStrategyProfile
    from portfolio.construct import construct, Constraints
    from quantlens.backtest_inputs import inputs
    profile=ResolvedStrategyProfile.model_validate(profile_payload) if profile_payload else StrategyProfileStore().resolve(strategy_id)
    end=as_of or spark.table('dwd.prices_clean').agg(F.max('date').alias('date')).first().date.isoformat()
    strategy_id=target_id or profile.extra.get('source_strategy_id',strategy_id)
    payload=inputs(profile,end,end,spark)
    date=payload['signal'].scores.index[-1]
    candidates=payload['signal'].scores.loc[date].rename('score').rename_axis('code').reset_index()
    candidates['cluster_id']=candidates.code.map(payload['clusters'].loc[date]).astype('string')
    candidates['industry_code']=candidates.code.map(payload['industries'].loc[date]).astype('string')
    candidates['in_universe']=candidates.code.map(payload['mask'].loc[date])
    candidates['close']=candidates.code.map(payload['market']['close'].loc[date])
    tradable=spark.table('dwd.prices_clean').where(F.col('date')==end).select('code','is_trading','is_suspended','limit_up','is_delisting').toPandas()
    candidates=candidates.merge(tradable,on='code',how='left')
    targets=construct(candidates,Constraints(**profile.constraints.model_dump()),breadth=payload['breadth'].get(date),regime=profile.risk.regime_filter.model_dump(),
                      star_market_enabled=star_market_enabled,capital=capital)
    candidates=candidates.drop(columns=['close'])
    targets=targets.drop(columns=['close'])
    signal=spark.createDataFrame(candidates.dropna(subset=['score'])).withColumn('strategy_id',F.lit(strategy_id)).withColumn('as_of',F.to_date(F.lit(end)))
    publish(spark,lineage(signal,context),'ads.strategy_signal',['strategy_id','as_of'],run=context)
    # Empty runs publish no new partition; account readers also filter by run_id.
    frame=(spark.createDataFrame(targets) if not targets.empty else
           spark.createDataFrame([],schema=signal.drop('strategy_id','as_of').schema.add('weight','double')))
    frame=frame.withColumn('strategy_id',F.lit(strategy_id)).withColumn('as_of',F.to_date(F.lit(end)))
    publish(spark,lineage(frame,context),'ads.portfolio_target',['strategy_id','as_of'],run=context,allow_empty=True)

if __name__=='__main__':
    import argparse
    import json
    parser=argparse.ArgumentParser();parser.add_argument('--strategy',required=True);parser.add_argument('--as-of');parser.add_argument('--batch');parser.add_argument('--profile');parser.add_argument('--no-star-market',action='store_true');parser.add_argument('--capital',type=float);parser.add_argument('--target-id');args=parser.parse_args()
    with job('portfolio') as (spark,context):
        run(spark,context,args.strategy,args.as_of,json.loads(args.profile) if args.profile else None,
            star_market_enabled=not args.no_star_market,capital=args.capital,target_id=args.target_id)
        if args.batch:
            from quantlens.spark_ads import export
            export(spark,context,args.batch)
