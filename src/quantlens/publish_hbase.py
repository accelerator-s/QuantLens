from __future__ import annotations
from storage.hbase import HBase,factor_rowkey


def run(spark,context):
    from pyspark.sql import functions as F
    # Executed from spark-client: use the cluster DNS name, not host loopback.
    client=HBase('http://hbase:16050')
    try:
        for name,families in [('factor_snapshot',['f','m']),('factor_by_code',['f','m']),('signal_latest',['s','m']),('holding_snapshot',['h','m']),('profile',['p','m'])]:
            client.create('ql:'+name,families,ttl=None if name in {'signal_latest','profile'} else 1095*86400)
        values=spark.table('dws.factor_values')
        latest=values.agg(F.max('date').alias('date')).first().date
        values=values.where(F.col('date')==latest)
        # Store factor versions in column qualifiers, preserving old implementations.
        grouped=values.groupBy('code','date').agg(F.collect_list(F.struct('factor','version','std_value','run_id','data_snapshot')).alias('factors'))
        for row in grouped.toLocalIterator():
            date=str(row.date);cells={'m:code':row.code,'m:date':date,'m:run_id':context['run_id'],'m:data_snapshot':context['data_snapshot']}
            for factor in row.factors:
                cells[f'f:{factor.factor}@{factor.version}']=factor.std_value
                cells[f'm:{factor.factor}@{factor.version}:run_id']=factor.run_id
            client.put('ql:factor_snapshot',factor_rowkey(row.code,date),cells)
            client.put('ql:factor_by_code',row.code+'|'+date,cells)
        if spark.catalog.tableExists('ads.strategy_signal'):
            signals=spark.table('ads.strategy_signal')
            dates=signals.groupBy('strategy_id').agg(F.max('as_of').alias('as_of'))
            for row in signals.join(dates,['strategy_id','as_of']).orderBy('strategy_id','code').toLocalIterator():
                client.put('ql:signal_latest',row.strategy_id+'|'+row.code,{'s:score':row.score,'m:code':row.code,'m:date':str(row.as_of),'m:run_id':row.run_id,'m:data_snapshot':row.data_snapshot})
        if spark.catalog.tableExists('ads.portfolio_target'):
            for row in spark.table('ads.portfolio_target').toLocalIterator():
                client.put('ql:holding_snapshot',row.strategy_id+'|'+str(row.as_of)+'|'+row.code,{'h:weight':row.weight,'m:code':row.code,'m:date':str(row.as_of),'m:run_id':row.run_id,'m:data_snapshot':row.data_snapshot})
        for row in spark.table('dwd.prices_clean').where(F.col('date')==latest).select('code','close','date','run_id','data_snapshot').toLocalIterator():
            client.put('ql:profile',row.code,{'p:close':row.close,'m:code':row.code,'m:date':str(row.date),'m:run_id':row.run_id,'m:data_snapshot':row.data_snapshot})
    finally: client.close()
