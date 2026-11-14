"""Bind published HBase snapshots into the shared Hive catalog."""
from storage.hive import execute
from storage.hbase import HBase
from paths import workspace


def ensure_hive_hbase():
    client=HBase()
    try:
        client.create('ql:profile',['p','m'],ttl=None)
        client.create('ql:factor_by_code',['f','m'])
    finally:client.close()
    ddl=workspace().root/'docker/ddl'
    sql=(ddl/'001_databases.sql').read_text()+'\n'+(ddl/'002_hbase.sql').read_text()
    execute(sql, timeout=300)
