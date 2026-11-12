import hashlib
import subprocess
from datetime import date
from functools import lru_cache

import duckdb
import pyarrow.parquet as pq

from exceptions import ContractViolation
from paths import workspace
from quantlens import store
from webui.backend.serialize import json_safe

def staged():
    return workspace().staged_for('CN-A', 'stock')

def query(sql, params=None):
    with duckdb.connect() as db:
        db.execute("SET threads=4")
        db.execute("SET memory_limit='2GB'")
        return json_safe(db.execute(sql, params or []).df().to_dict('records'))

def table(name):
    path = staged() / f'{name}.parquet'
    if not path.is_file():
        raise ContractViolation(code='QL_DATA_NOT_FOUND', message=f'缺少数据表：{name}', suggested_action='在数据页完成同步')
    return str(path)

def inventory():
    return [{'table': p.stem, 'rows': pq.ParquetFile(p).metadata.num_rows,
             'bytes': p.stat().st_size, 'fields': pq.ParquetFile(p).schema_arrow.names}
            for p in sorted(staged().glob('*.parquet'))]

@lru_cache(maxsize=4)
def _coverage(signature):
    return query('SELECT min(date)::DATE AS start, max(date)::DATE AS end, count(*) AS rows, '
                 'count(DISTINCT instrument_id) AS instruments, count(DISTINCT date) AS days '
                 'FROM read_parquet(?)', [table('prices_daily')])[0]

def coverage():
    p = staged() / 'prices_daily.parquet'
    value = dict(_coverage(p.stat().st_mtime_ns))
    value['age_days'] = (date.today() - date.fromisoformat(value['end'][:10])).days
    value['mode'] = 'historical' if value['age_days'] > 5 else 'recent'
    return value

def market(as_of=None):
    end = as_of or coverage()['end'][:10]
    return query('''SELECT date::DATE AS date, count(*) AS stocks,
        avg(ret_wd) AS equal_weight_return, median(ret_wd) AS median_return,
        sum(CASE WHEN ret_wd>0 THEN 1 ELSE 0 END) AS advancing,
        sum(CASE WHEN ret_wd<0 THEN 1 ELSE 0 END) AS declining
        FROM read_parquet(?) WHERE CAST(date AS DATE)<=CAST(? AS DATE) GROUP BY date
        ORDER BY date DESC LIMIT 120''', [table('returns_daily'), end])

def stock(code, as_of=None):
    end = as_of or coverage()['end'][:10]
    prices = query('''SELECT p.*, r.ret_wd, r.adj_close_wd, r.mktcap_total, r.trading_status
        FROM read_parquet(?) p LEFT JOIN read_parquet(?) r USING(instrument_id,date)
        WHERE p.instrument_id=? AND CAST(p.date AS DATE)<=CAST(? AS DATE) ORDER BY p.date DESC LIMIT 500''',
        [table('prices_daily'), table('returns_daily'), code, end])
    if not prices:
        raise ContractViolation(code='QL_STOCK_NOT_FOUND', message='该日期前没有此证券行情', suggested_action='检查证券代码和日期')
    events = {}
    for name, when, extra in [
        ('fs_income', 'effective_date', 'AND ann_date_imputed IS NULL'),
        ('fs_balance', 'effective_date', 'AND ann_date_imputed IS NULL'),
        ('fs_cashflow', 'effective_date', 'AND ann_date_imputed IS NULL'),
        ('fundamental_ttm', 'effective_date', 'AND ann_date_imputed IS NULL'),
        ('consensus_daily', 'effective_date', ''),
        ('holder_longline', 'effective_date', ''),
        ('industry_classification', 'effective_date', "AND classification_id='P0221'"),
    ]:
        events[name] = query(f'''SELECT * FROM read_parquet(?) WHERE instrument_id=?
            AND CAST({when} AS DATE)<=CAST(? AS DATE) {extra}
            ORDER BY {when} DESC LIMIT 12''', [table(name), code, end])
    timeline = [r for r in store.records('watch_event', 2000) if r['body']['instrument_id'] == code]
    return {'instrument_id': code, 'as_of': end, 'prices': prices[::-1], 'events': events,
            'timeline': timeline, 'pit_note': '按已知披露日筛选；源表更正前版本尚不完整。'}

def screen(as_of=None, max_price=100000, min_amount=0, limit=100, exclude_st=True):
    end = as_of or coverage()['end'][:10]
    return query('''SELECT p.*, r.ret_wd, r.mktcap_total, r.trading_status
        FROM read_parquet(?) p JOIN read_parquet(?) r USING(instrument_id,date)
        WHERE p.date=(SELECT max(date) FROM read_parquet(?) WHERE CAST(date AS DATE)<=CAST(? AS DATE))
        AND p.close>0 AND p.close<=? AND p.amount>=? AND p.volume>0
        AND (NOT ? OR CAST(r.trading_status AS INTEGER) NOT IN (2,3,5,6,8,9,11,12,14,15,16))
        ORDER BY p.amount DESC, p.instrument_id LIMIT ?''',
        [table('prices_daily'), table('returns_daily'), table('prices_daily'), end,
         max_price, min_amount, exclude_st, limit])

def quality():
    p, r = table('prices_daily'), table('returns_daily')
    stats = query('''SELECT count(*) AS rows, count(*)-count(DISTINCT (instrument_id,date)) AS duplicates,
        count(*) FILTER(WHERE close IS NULL OR close<=0) AS invalid_close,
        count(*) FILTER(WHERE volume IS NULL OR volume<0) AS invalid_volume,
        count(*) FILTER(WHERE high<low OR close>high OR close<low) AS invalid_ohlc
        FROM read_parquet(?)''', [p])[0]
    stats['unmatched'] = query('''SELECT count(*) AS n FROM read_parquet(?) p
        ANTI JOIN read_parquet(?) r USING(instrument_id,date)''', [p, r])[0]['n']
    stats['years'] = query('''SELECT year(CAST(date AS DATE)) AS year, count(*) AS rows,
        count(DISTINCT instrument_id) AS stocks FROM read_parquet(?) GROUP BY 1 ORDER BY 1''', [p])
    stats['passed'] = not any(stats[k] for k in ['duplicates','invalid_close','invalid_volume','invalid_ohlc','unmatched'])
    return store.add('quality', stats)

def freeze(reporter):
    digest = hashlib.sha256()
    files = []
    for name in ['prices_daily','returns_daily']:
        p = staged() / f'{name}.parquet'
        h = hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()
        digest.update(h.encode())
        files.append({'table': name, 'sha256': h, 'bytes': p.stat().st_size})
    dataset_id = 'DATA-' + digest.hexdigest()[:20]
    root = workspace().data / 'snapshots' / dataset_id
    root.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = root / (f['table'] + '.parquet')
        if not target.exists():
            subprocess.run(['cp', '--reflink=auto', table(f['table']), str(target)], check=True)
        with target.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != f['sha256']:
                raise ContractViolation(code='QL_SNAPSHOT_CHANGED', message='快照复制校验失败', suggested_action='停止同步后重试')
    manifest = {'dataset_id': dataset_id, 'files': files, 'coverage': coverage(),
                'source': 'CSMAR / Quant_Training import', 'created_at': store.now()}
    if not (root / 'snapshot.json').exists():
        store.write_json(root / 'snapshot.json', manifest)
        store.add('dataset', manifest, dataset_id)
    reporter(f'冻结数据：{dataset_id}')
    return dataset_id, root
