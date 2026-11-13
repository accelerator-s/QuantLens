"""Check published business rows, portfolio limits and Hive/HBase integration."""
import json
import time
from pathlib import Path

import numpy as np

from quantlens import ads
from quantlens.integrations import ensure_hive_hbase
from storage.hbase import HBase, factor_rowkey
from storage.hdfs import compose
from strategy import StrategyProfileStore

manifest = ads.manifest()
as_of = manifest['data_as_of']
report = {'data_as_of': as_of, 'run_id': manifest['run_id'],
          'data_snapshot': manifest['data_snapshot'], 'portfolios': []}
targets = ads.query('portfolio_target', limit=10000, order='as_of DESC, strategy_id, code')
latest = {}
for row in targets:
    latest.setdefault(row['strategy_id'], row['as_of'])
for strategy, date in latest.items():
    rows = [r for r in targets if r['strategy_id'] == strategy and r['as_of'] == date]
    limits = StrategyProfileStore().resolve(strategy).constraints
    cluster = {}; industry = {}
    for row in rows:
        cluster[row['cluster_id']] = cluster.get(row['cluster_id'], 0) + row['weight']
        industry[row['industry_code']] = industry.get(row['industry_code'], 0) + row['weight']
    check = {'strategy': strategy, 'as_of': date, 'positions': len(rows),
             'total_weight': sum(r['weight'] for r in rows),
             'max_weight': max(r['weight'] for r in rows),
             'max_cluster_weight': max(cluster.values()),
             'max_industry_weight': max(industry.values())}
    assert check['positions'] <= limits.max_positions
    assert check['total_weight'] <= 1 - limits.cash_reserve + 1e-9
    for field in ['max_weight', 'max_cluster_weight', 'max_industry_weight']:
        assert check[field] <= getattr(limits, field) + 1e-9, check
    report['portfolios'].append(check)
assert report['portfolios'], 'No published portfolio targets'

client = HBase()
try:
    expected = ads.query('factor_values', 'factor=? AND code=? AND date=?',
                         ['reversal_5', '000001', as_of], limit=1)[0]
    key = factor_rowkey('000001', as_of)
    row = client.get('ql:factor_snapshot', key)
    qualifier = 'reversal_5@' + expected['version']
    assert row['m:' + qualifier + ':run_id'] == expected['run_id']
    assert abs(float(row['f:' + qualifier]) - expected['std_value']) <= 1e-12
    sample = targets[0]
    prefix = sample['strategy_id'] + '|' + str(sample['as_of']) + '|'
    times = {'get': [], 'scan': []}
    for _ in range(30):
        started = time.perf_counter(); assert client.get('ql:factor_snapshot', key)
        times['get'].append(time.perf_counter() - started)
        started = time.perf_counter(); holdings = client.scan('ql:holding_snapshot', prefix, 100)
        times['scan'].append(time.perf_counter() - started)
    assert {r['m:code'] for r in holdings} == {
        r['code'] for r in targets if r['strategy_id'] == sample['strategy_id'] and r['as_of'] == sample['as_of']}
    report['hbase'] = {name: {'p95_seconds': float(np.percentile(values, 95)), 'samples': len(values)}
                       for name, values in times.items()}
    assert all(item['p95_seconds'] < .05 for item in report['hbase'].values()), report['hbase']
finally:
    client.close()

ensure_hive_hbase()
sql = ("SELECT code, `date`, `close` FROM ods.profile_hbase WHERE rowkey='000001'; "
       f"SELECT code, `date`, f['{qualifier}'] FROM dws.factor_snapshot_hbase "
       f"WHERE rowkey='000001|{as_of}';")
result = compose('exec', '-T', 'hive', 'beeline', '-u', 'jdbc:hive2://localhost:10000',
                 '-n', 'root', '--silent=true', '--showHeader=false', '--outputformat=tsv2', '-e', sql)
lines = [line.split('\t') for line in result.stdout.splitlines() if line.startswith('000001\t')]
assert len(lines) == 2 and all(row[1] == as_of for row in lines), result.stdout
assert abs(float(lines[1][2]) - expected['std_value']) <= 1e-12
report['hive_hbase'] = {'queries': sql, 'rows': lines, 'passed': True}
Path('docs/course/evidence/published-contracts.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
print(json.dumps(report, ensure_ascii=False))
