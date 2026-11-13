CREATE EXTERNAL TABLE IF NOT EXISTS ods.fs_cashflow (
  `instrument_id` string,
  `report_period` string,
  `statement_type` string,
  `cfo_net` double,
  `capex_cash` double,
  `cfi_net` double,
  `cff_net` double,
  `cash_net_increase` double,
  `downloaded_at` string,
  `ann_date` string,
  `effective_date` string,
  `ann_date_imputed` string
)
PARTITIONED BY (year int) STORED AS PARQUET
LOCATION '/quantlens/ods/fs_cashflow'
TBLPROPERTIES ('parquet.compression'='snappy');
