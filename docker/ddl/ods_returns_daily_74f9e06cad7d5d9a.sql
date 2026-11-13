CREATE EXTERNAL TABLE IF NOT EXISTS ods.returns_daily (
  `instrument_id` string,
  `date` string,
  `mktcap_float` double,
  `mktcap_total` double,
  `ret_wd` double,
  `ret_nd` double,
  `adj_close_wd` double,
  `adj_close_nd` double,
  `trading_status` bigint
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/returns_daily'
TBLPROPERTIES ('parquet.compression'='snappy');
