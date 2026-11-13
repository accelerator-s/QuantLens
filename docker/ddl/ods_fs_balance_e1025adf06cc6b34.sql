CREATE EXTERNAL TABLE IF NOT EXISTS ods.fs_balance (
  `instrument_id` string,
  `report_period` string,
  `statement_type` string,
  `cash` double,
  `accounts_receivable` double,
  `inventory` double,
  `current_assets` double,
  `fixed_assets` double,
  `total_assets` double,
  `short_term_debt` double,
  `current_liabilities` double,
  `long_term_debt` double,
  `total_liabilities` double,
  `equity_parent` double,
  `total_equity` double,
  `downloaded_at` string,
  `ann_date` string,
  `effective_date` string,
  `ann_date_imputed` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/fs_balance'
TBLPROPERTIES ('parquet.compression'='snappy');
