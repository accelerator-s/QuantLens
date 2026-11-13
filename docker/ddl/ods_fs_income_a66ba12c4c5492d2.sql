CREATE EXTERNAL TABLE IF NOT EXISTS ods.fs_income (
  `instrument_id` string,
  `report_period` string,
  `statement_type` string,
  `total_revenue` double,
  `revenue` double,
  `total_operating_cost` double,
  `operating_cost` double,
  `operating_profit` double,
  `profit_before_tax` double,
  `net_profit` double,
  `net_profit_parent` double,
  `downloaded_at` string,
  `ann_date` string,
  `effective_date` string,
  `ann_date_imputed` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/fs_income'
TBLPROPERTIES ('parquet.compression'='snappy');
