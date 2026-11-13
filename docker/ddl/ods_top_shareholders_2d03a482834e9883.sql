CREATE EXTERNAL TABLE IF NOT EXISTS ods.top_shareholders (
  `instrument_id` string,
  `report_period` string,
  `shareholder_name` string,
  `rank` bigint,
  `shares_held` double,
  `pct_of_total` double,
  `share_nature` string,
  `shareholder_nature` string,
  `source_category_id` string,
  `source_category` string,
  `downloaded_at` string,
  `ann_date` string,
  `holder_type` string,
  `holder_type_rule` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/top_shareholders'
TBLPROPERTIES ('parquet.compression'='snappy');
