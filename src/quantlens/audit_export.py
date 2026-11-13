from pathlib import Path
import pandas as pd
from paths import workspace
from quantlens.store import connect


def export():
    with connect() as db:
        frame=pd.read_sql_query('SELECT id,kind,created_at,body,sha256 FROM records ORDER BY created_at,id',db)
    path=workspace().data/'landing/audit/records.parquet'
    path.parent.mkdir(parents=True,exist_ok=True)
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema=pa.schema([pa.field(name,pa.string()) for name in ['id','kind','created_at','body','sha256']])
    pq.write_table(pa.Table.from_pandas(frame,schema=schema,preserve_index=False),path,compression='snappy')
    return path
