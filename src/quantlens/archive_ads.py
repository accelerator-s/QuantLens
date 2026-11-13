"""Move expired serving partitions to research archive without deleting records."""
from datetime import date,timedelta
from urllib.parse import unquote
from quantlens.warehouse import root_uri,_fs,write_json,identifier


def run(spark,context,as_of=None):
    reference=as_of or spark.sql('SELECT current_date()').first()[0]
    try:cutoff=reference.replace(year=reference.year-3)
    except ValueError:cutoff=reference.replace(year=reference.year-3,day=28)
    for record in spark.catalog.listTables('ads'):
        table='ads.'+identifier(record.name)
        if not spark.catalog.tableExists(table):continue
        if not any(col.name=='as_of' and col.isPartition for col in spark.catalog.listColumns(table)):continue
        for row in spark.sql(f'SHOW PARTITIONS {table}').collect():
            parts=dict(part.split('=',1) for part in row[0].split('/'))
            if date.fromisoformat(unquote(parts['as_of']))>=cutoff:continue
            spec=','.join(f"`{identifier(key)}`='{unquote(value).replace(chr(39),chr(39)*2)}'" for key,value in parts.items())
            details=spark.sql(f'DESCRIBE FORMATTED {table} PARTITION ({spec})').collect()
            source=next(item.data_type for item in details if item.col_name.strip()=='Location')
            if '/archive/ads/' in source:continue
            import hashlib
            location_id=hashlib.sha256(source.encode()).hexdigest()[:20]
            archive=f'{root_uri()}/archive/ads/{record.name}/{row[0]}/{location_id}'
            fs,origin=_fs(spark,source);_,destination=_fs(spark,archive)
            write_json(spark,f'{root_uri()}/meta/runs/archive/{context["run_id"]}/{record.name}/{row[0]}/intent',
                       {'table':table,'partition':parts,'source_uri':source,'archive_uri':archive,**context})
            fs.mkdirs(destination.getParent())
            if fs.exists(origin):
                if fs.exists(destination) or not fs.rename(origin,destination):raise RuntimeError('ADS archive move failed')
            elif not fs.exists(destination):raise RuntimeError('ADS archive source missing')
            spark.sql(f"ALTER TABLE {table} PARTITION ({spec}) SET LOCATION '{archive}'")
        spark.catalog.refreshTable(table)
