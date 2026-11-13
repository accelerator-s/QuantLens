CREATE EXTERNAL TABLE IF NOT EXISTS ods.disclosure_dates (
  `instrument_id` string,
  `report_period` string,
  `report_type` bigint,
  `ann_date` string,
  `downloaded_at` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/disclosure_dates'
TBLPROPERTIES ('parquet.compression'='snappy');
