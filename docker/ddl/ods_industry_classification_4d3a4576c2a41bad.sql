CREATE EXTERNAL TABLE IF NOT EXISTS ods.industry_classification (
  `instrument_id` string,
  `classification_id` string,
  `classification_name` string,
  `effective_date` string,
  `industry_code` string,
  `industry_name` string,
  `downloaded_at` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/industry_classification'
TBLPROPERTIES ('parquet.compression'='snappy');
