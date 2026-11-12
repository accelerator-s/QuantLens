from pyspark.sql import Window, functions as F

FEATURES = [
    'momentum_5','momentum_10','momentum_20','momentum_60',
    'bias_5','bias_20','bias_60','volatility_10','volatility_20',
    'range_1','volume_ratio','amount_ratio','log_amount','position_60',
    'high_gap_60','illiquidity_20',
    'momentum_1','momentum_3','bias_10','volatility_5','volatility_60',
    'downside_20','atr_14','price_volume_corr_20','volume_change_5','amount_change_5',
    'turnover_total_20','volume_cv_20','high_gap_20','skewness_20',
]
LABELS = ['5日动量','10日动量','20日动量','60日动量','5日均线偏离','20日均线偏离',
          '60日均线偏离','10日波动','20日波动','日内振幅','量比','成交额比',
          '成交额对数','60日价格位置','距60日高点','20日非流动性',
          '1日动量','3日动量','10日均线偏离','5日波动','60日波动',
          '20日下行波动','14日真实波幅','20日量价相关','5日成交量变化','5日成交额变化',
          '20日总股本换手估算','20日成交量变异','距20日高点','20日收益偏度']

def build(spark, source, start, end, stock_limit=0):
    prices = spark.read.parquet(str(source / 'prices_daily.parquet')).withColumn('date', F.to_date('date'))
    returns = spark.read.parquet(str(source / 'returns_daily.parquet')).withColumn('date', F.to_date('date'))
    calendar = prices.select('date').distinct().withColumn('td', F.row_number().over(Window.orderBy('date')))
    prices = prices.where((F.col('date') >= F.date_sub(F.lit(start), 160)) & (F.col('date') <= F.lit(end)))
    if stock_limit:
        codes = prices.select('instrument_id').distinct().orderBy('instrument_id').limit(stock_limit)
        prices = prices.join(F.broadcast(codes), 'instrument_id')
    returns = returns.where((F.col('date') >= F.date_sub(F.lit(start), 160)) & (F.col('date') <= F.lit(end)))
    df = prices.join(returns, ['instrument_id','date']).join(F.broadcast(calendar), 'date')
    w = Window.partitionBy('instrument_id').orderBy('td')
    close = F.col('adj_close_wd')
    for n in [1,3,5,10,20,60]:
        df = df.withColumn(f'momentum_{n}', F.when(F.col('td')-F.lag('td',n).over(w)==n,
                          close / F.lag('adj_close_wd',n).over(w)-1))
    for n in [5,10,20,60]:
        frame = w.rowsBetween(-n+1,0)
        df = df.withColumn(f'bias_{n}', F.when(F.count('adj_close_wd').over(frame)==n,
                          close/F.avg('adj_close_wd').over(frame)-1))
    for n in [5,10,20,60]:
        frame = w.rowsBetween(-n+1,0)
        df = df.withColumn(f'volatility_{n}', F.when(F.count('ret_wd').over(frame)==n,
                          F.stddev_samp('ret_wd').over(frame)))
    w20, w60 = w.rowsBetween(-19,0), w.rowsBetween(-59,0)
    df = (df.withColumn('range_1', (F.col('high')-F.col('low'))/F.col('close'))
        .withColumn('volume_ratio', F.try_divide(F.col('volume'),F.avg('volume').over(w20)))
        .withColumn('amount_ratio', F.try_divide(F.col('amount'),F.avg('amount').over(w20)))
        .withColumn('log_amount', F.log1p('amount'))
        .withColumn('position_60', F.try_divide(close-F.min('adj_close_wd').over(w60),
                                            F.max('adj_close_wd').over(w60)-F.min('adj_close_wd').over(w60)))
        .withColumn('high_gap_60', close/F.max('adj_close_wd').over(w60)-1)
        .withColumn('illiquidity_20',F.avg(F.try_divide(F.abs('ret_wd'),F.col('amount'))).over(w20)*1e8)
        .withColumn('window_valid', (F.col('td')-F.lag('td',59).over(w)==59)))
    prior=F.lag('adj_close_wd').over(w)
    df=(df.withColumn('_adj_high',F.col('high')*close/F.col('close'))
        .withColumn('_adj_low',F.col('low')*close/F.col('close'))
        .withColumn('_tr',F.greatest(F.col('_adj_high')-F.col('_adj_low'),
                    F.abs(F.col('_adj_high')-prior),F.abs(F.col('_adj_low')-prior))/close)
        .withColumn('downside_20',F.sqrt(F.avg(F.pow(F.least(F.col('ret_wd'),F.lit(0.)),2)).over(w20)))
        .withColumn('atr_14',F.avg('_tr').over(w.rowsBetween(-13,0)))
        .withColumn('price_volume_corr_20',F.try_divide(F.covar_pop('adj_close_wd','volume').over(w20),F.stddev_pop('adj_close_wd').over(w20)*F.stddev_pop('volume').over(w20)))
        .withColumn('volume_change_5',F.try_divide(F.col('volume'),F.lag('volume',5).over(w))-1)
        .withColumn('amount_change_5',F.try_divide(F.col('amount'),F.lag('amount',5).over(w))-1)
        .withColumn('turnover_total_20',F.avg(F.try_divide(F.col('volume')*F.col('close'),F.col('mktcap_total')*1000)).over(w20))
        .withColumn('volume_cv_20',F.try_divide(F.stddev_samp('volume').over(w20),F.avg('volume').over(w20)))
        .withColumn('high_gap_20',close/F.max('adj_close_wd').over(w20)-1)
        .withColumn('skewness_20',F.try_divide(F.avg(F.pow('ret_wd',3)).over(w20)-3*F.avg('ret_wd').over(w20)*F.avg(F.pow('ret_wd',2)).over(w20)+2*F.pow(F.avg('ret_wd').over(w20),3),F.pow(F.var_pop('ret_wd').over(w20),1.5))))
    future = df.select('instrument_id','td','date','adj_close_wd','close','volume','high','low')
    for offset, prefix in [(1,'entry'),(6,'exit')]:
        shifted = future.select('instrument_id', (F.col('td')-offset).alias('td'),
            *[F.col(c).alias(f'{prefix}_{c}') for c in ['date','adj_close_wd','close','volume','high','low']])
        df = df.join(shifted,['instrument_id','td'],'left')
    df = (df.withColumn('label',F.try_divide(F.col('exit_adj_close_wd'),F.col('entry_adj_close_wd'))-1)
        .withColumn('label_end',F.col('exit_date'))
        .withColumn('eligible',(F.col('close')>0)&(F.col('volume')>0)&F.col('window_valid') &
            ~F.col('trading_status').cast('int').isin(2,3,5,6,8,9,11,12,14,15,16))
        .where(F.col('date')>=F.lit(start)))
    return df.select('instrument_id','date','td','close','open','high','low','volume','amount',
        'adj_close_wd','ret_wd','mktcap_total','trading_status','eligible','entry_date','exit_date',
        'entry_close','exit_close','entry_volume','exit_volume','entry_high','entry_low',
        'exit_high','exit_low','label','label_end',*FEATURES)
