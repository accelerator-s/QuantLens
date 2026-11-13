CREATE EXTERNAL TABLE IF NOT EXISTS ods.top_float_shareholders (
  `instrument_id` string,
  `report_period` string,
  `shareholder_name` string,
  `rank` bigint,
  `shares_held` double,
  `share_nature` string,
  `pct_of_total` double,
  `pct_of_float` double,
  `pct_of_float_a` double,
  `downloaded_at` string,
  `ann_date` string,
  `holder_type` string,
  `holder_type_rule` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/top_float_shareholders'
TBLPROPERTIES ('parquet.compression'='snappy');
