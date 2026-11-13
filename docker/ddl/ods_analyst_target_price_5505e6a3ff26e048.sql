CREATE EXTERNAL TABLE IF NOT EXISTS ods.analyst_target_price (
  `instrument_id` string,
  `source_record_key` string,
  `report_id` string,
  `report_date` string,
  `price_term` string,
  `publish_date` string,
  `target_price_max` double,
  `target_price_min` double,
  `downloaded_at` string,
  `publish_date_source` string,
  `institution_id` string,
  `institution_name` string,
  `analyst_id` string,
  `analyst_name` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/analyst_target_price'
TBLPROPERTIES ('parquet.compression'='snappy');
