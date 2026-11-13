CREATE EXTERNAL TABLE IF NOT EXISTS ods.profile_hbase (
 `rowkey` string, `code` string, `date` string, `close` double, `run_id` string
) STORED BY 'org.apache.hadoop.hive.hbase.HBaseStorageHandler'
WITH SERDEPROPERTIES ('hbase.columns.mapping'=':key,m:code,m:date,p:close,m:run_id')
TBLPROPERTIES ('hbase.table.name'='ql:profile');
CREATE EXTERNAL TABLE IF NOT EXISTS dws.factor_snapshot_hbase (
 `rowkey` string, `code` string, `date` string, `f` map<string,double>, `run_id` string
) STORED BY 'org.apache.hadoop.hive.hbase.HBaseStorageHandler'
WITH SERDEPROPERTIES ('hbase.columns.mapping'=':key,m:code,m:date,f:,m:run_id')
TBLPROPERTIES ('hbase.table.name'='ql:factor_by_code');
