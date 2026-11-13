CREATE EXTERNAL TABLE IF NOT EXISTS ods.analyst_rating (
  `instrument_id` string,
  `source_record_key` string,
  `report_id` string,
  `accounting_period` string,
  `report_date` string,
  `forecast_period` string,
  `analyst_name` string,
  `publish_date` string,
  `analyst_id` string,
  `institution_id` string,
  `institution_name` string,
  `rating_raw` string,
  `rating_standard` string,
  `rating_change` string,
  `rating_convention` string,
  `forecast_accuracy` double,
  `forecast_optimism` double,
  `revision_horizon` double,
  `forecast_horizon` double,
  `forecast_year` string,
  `analyst_key` string,
  `downloaded_at` string,
  `publish_date_source` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/analyst_rating'
TBLPROPERTIES ('parquet.compression'='snappy');
