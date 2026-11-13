CREATE EXTERNAL TABLE IF NOT EXISTS ods.instruments (
  `instrument_id` string,
  `source` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/instruments'
TBLPROPERTIES ('parquet.compression'='snappy');
