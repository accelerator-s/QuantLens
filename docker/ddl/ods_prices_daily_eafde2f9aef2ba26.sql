CREATE EXTERNAL TABLE IF NOT EXISTS ods.prices_daily (
  `instrument_id` string,
  `date` string,
  `open` double,
  `high` double,
  `low` double,
  `close` double,
  `volume` bigint,
  `amount` double
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/prices_daily'
TBLPROPERTIES ('parquet.compression'='snappy');
