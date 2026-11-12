import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from exceptions import ContractViolation
from paths import workspace
from webui.backend.serialize import json_safe

def now():
    return datetime.now(timezone.utc).isoformat()

def uid(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:16]}'

def encode(value):
    return json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, allow_nan=False)

@contextmanager
def connect():
    path = workspace().experience / 'quantlens.sqlite'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL,
          body TEXT NOT NULL, sha256 TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS record_kind ON records(kind, created_at);
        CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records
        BEGIN SELECT RAISE(ABORT, '不可覆盖历史'); END;
        CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
        BEGIN SELECT RAISE(ABORT, '不可删除历史'); END;
    ''')
    try:
        yield db
        db.commit()
    finally:
        db.close()

def add(kind, body, record_id=None):
    text = encode(body)
    record = {'id': record_id or uid(kind.upper()), 'kind': kind, 'created_at': now(),
              'body': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
    with connect() as db:
        db.execute('INSERT INTO records VALUES (:id,:kind,:created_at,:body,:sha256)', record)
    return {**record, 'body': json.loads(text)}

def get(record_id):
    with connect() as db:
        row = db.execute('SELECT * FROM records WHERE id=?', [record_id]).fetchone()
    if row is None:
        raise ContractViolation(code='QL_RECORD_NOT_FOUND', message='记录不存在', suggested_action='刷新列表后重试')
    item = dict(row)
    if hashlib.sha256(item['body'].encode()).hexdigest() != item['sha256']:
        raise ContractViolation(code='QL_HASH_MISMATCH', message='历史记录校验失败', suggested_action='恢复已核验的备份')
    item['body'] = json.loads(item['body'])
    return item

def records(kind, limit=100):
    with connect() as db:
        rows = db.execute('SELECT id FROM records WHERE kind=? ORDER BY created_at DESC LIMIT ?', [kind, limit]).fetchall()
    return [get(row['id']) for row in rows]

def run_path(run_id):
    if not re.fullmatch(r'QL-[a-f0-9]{16}', run_id):
        raise ContractViolation(code='QL_RUN_ID_INVALID', message='实验编号无效', suggested_action='从实验列表选择')
    return workspace().runs / 'spark' / run_id

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(encode(value), encoding='utf-8')
    temp.replace(path)
