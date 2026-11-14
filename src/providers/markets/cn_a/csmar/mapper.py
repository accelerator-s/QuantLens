from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from providers.exceptions import ProviderContractError

PRICES_DAILY_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Trddt": "date",
    "Opnprc": "open",
    "Hiprc": "high",
    "Loprc": "low",
    "Clsprc": "close",
    "Dnshrtrd": "volume",
    "Dnvaltrd": "amount",
}

INSTRUMENTS_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
}

RETURNS_DAILY_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Trddt": "date",
    "Dretwd": "ret_wd",
    "Dretnd": "ret_nd",
    "Adjprcwd": "adj_close_wd",
    "Adjprcnd": "adj_close_nd",
    "Dsmvosd": "mktcap_float",
    "Dsmvtll": "mktcap_total",
    "Trdsta": "trading_status",
}

FS_BALANCE_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Accper": "report_period",
    "Typrep": "statement_type",
    "A001101000": "cash",
    "A001111000": "accounts_receivable",
    "A001123000": "inventory",
    "A001100000": "current_assets",
    "A001212000": "fixed_assets",
    "A001000000": "total_assets",
    "A002101000": "short_term_debt",
    "A002100000": "current_liabilities",
    "A002201000": "long_term_debt",
    "A002000000": "total_liabilities",
    "A003100000": "equity_parent",
    "A003000000": "total_equity",
}

FS_INCOME_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Accper": "report_period",
    "Typrep": "statement_type",
    "B001100000": "total_revenue",
    "B001101000": "revenue",
    "B001200000": "total_operating_cost",
    "B001201000": "operating_cost",
    "B001300000": "operating_profit",
    "B001000000": "profit_before_tax",
    "B002000000": "net_profit",
    "B002000101": "net_profit_parent",
}

FS_CASHFLOW_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Accper": "report_period",
    "Typrep": "statement_type",
    "C001000000": "cfo_net",
    "C002000000": "cfi_net",
    "C002006000": "capex_cash",
    "C003000000": "cff_net",
    "C005000000": "cash_net_increase",
}

DISCLOSURE_DATES_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Accper": "report_period",
    "Reptyp": "report_type",
    "Annodt": "ann_date",
}

INDUSTRY_CLASSIFICATION_MAP: dict[str, str] = {
    "Symbol": "instrument_id",
    "IndustryClassificationID": "classification_id",
    "IndustryClassificationName": "classification_name",
    "ImplementDate": "effective_date",
    "IndustryCode": "industry_code",
    "IndustryName": "industry_name",
}

TOP_SHAREHOLDERS_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Reptdt": "report_period",
    "S0301a": "shareholder_name",
    "S0306a": "rank",
    "S0302a": "shares_held",
    "S0304a": "pct_of_total",
    "S0305a": "share_nature",
    "ShareholderNature": "shareholder_nature",
    "CategoryID": "source_category_id",
    "Category": "source_category",
}

TOP_FLOAT_SHAREHOLDERS_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Reptdt": "report_period",
    "S0401a": "shareholder_name",
    "S0404a": "rank",
    "S0402a": "shares_held",
    "ShareholdingRatio": "pct_of_total",
    "SharesTradSharesRatio": "pct_of_float",
    "SharesTradASharesRatio": "pct_of_float_a",
    "S0403a": "share_nature",
}

INST_HOLDINGS_MAP: dict[str, str] = {
    "Symbol": "instrument_id",
    "EndDate": "report_period",
    "FundHoldShares": "fund_shares",
    "FundHoldProportion": "fund_pct_total",
    "FundHoldProportion1": "fund_pct_float",
    "QFIIHoldShares": "qfii_shares",
    "QFIIHoldProportion": "qfii_pct_total",
    "QFIIHoldProportion1": "qfii_pct_float",
    "BrokerHoldShares": "broker_shares",
    "BrokerHoldProportion": "broker_pct_total",
    "BrokerHoldProportion1": "broker_pct_float",
    "InsuranceHoldShares": "insurance_shares",
    "InsuranceHoldProportion": "insurance_pct_total",
    "InsuranceHoldProportion1": "insurance_pct_float",
    "SecurityFundHoldShares": "social_security_shares",
    "SecurityFundHoldProportion": "social_security_pct_total",
    
    "SecurityFundHProportion1": "social_security_pct_float",
    "EntrustHoldShares": "trust_shares",
    "EntrustHoldProportion": "trust_pct_total",
    "EntrustHoldProportion1": "trust_pct_float",
    "FinanceHoldShares": "finance_co_shares",
    "FinanceHoldProportion": "finance_co_pct_total",
    "FinanceHoldProportion1": "finance_co_pct_float",
    "BankHoldShares": "bank_shares",
    "BankHoldProportion": "bank_pct_total",
    "BankHoldProportion1": "bank_pct_float",
    "NonFinanceHoldShares": "nonfin_corp_shares",
    "NonFinanceHoldProportion": "nonfin_corp_pct_total",
    "NonFinanceHoldProportion1": "nonfin_corp_pct_float",
    "OtherHoldShares": "other_shares",
    "OtherHoldProportion": "other_pct_total",
    "OtherHoldProportion1": "other_pct_float",
    "TotalHoldShares": "inst_total_shares",
    "InsInvestorProp": "inst_pct_total",
    "InsInvestorProp1": "inst_pct_float",
}

ANALYST_FORECAST_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "Rptdt": "report_date",
    "Fenddt": "forecast_period",
    "ReportID": "report_id",
    "DeclareDate": "publish_date",
    "AnanmID": "analyst_id",
    "Ananm": "analyst_name",
    "InstitutionID": "institution_id",
    "Brokern": "institution_name",
    "Feps": "forecast_eps",
    "Fpe": "forecast_pe",
    "Fnetpro": "forecast_net_profit",
    "Febit": "forecast_ebit",
    "Febitda": "forecast_ebitda",
    "Fturnover": "forecast_revenue",
    "Fcfps": "forecast_cfps",
    "FBPS": "forecast_bps",
    "FROA": "forecast_roa",
    "FROE": "forecast_roe",
    "FPB": "forecast_pb",
    "FTotalAssetsTurnover": "forecast_total_asset_turnover",
    "ProfitParent": "forecast_net_profit_parent",
    "TotalProfit": "forecast_total_profit",
    "OperationProfit": "forecast_operating_profit",
    "TotalAsset": "forecast_total_assets",
    "TotalLiabilities": "forecast_total_liabilities",
}

ANALYST_RATING_MAP: dict[str, str] = {
    "Stkcd": "instrument_id",
    "ReportID": "report_id",
    "Accper": "accounting_period",
    "Rptdt": "report_date",
    "Fenddt": "forecast_period",
    "Ananm": "analyst_name",
    "DeclareDate": "publish_date",
    "AnanmID": "analyst_id",
    "InstitutionID": "institution_id",
    "Brokern": "institution_name",
    "Investrank": "rating_raw",
    "Stdrank": "rating_standard",
    "Rankchg": "rating_change",
    "Rankbench": "rating_convention",
    "ForecastAccuracy": "forecast_accuracy",
    "ForecastOptimism": "forecast_optimism",
    "RivisionHorizon": "revision_horizon",
    "ForecastHorizon": "forecast_horizon",
}

ANALYST_TARGET_PRICE_MAP: dict[str, str] = {
    "ReportID": "report_id",
    "Stkcd": "instrument_id",
    "ReportDate": "report_date",
    "PriceTerm": "price_term",
    "DeclareDate": "publish_date",
    "ObjectPriceMax": "target_price_max",
    "ObjectPriceMin": "target_price_min",
}

TABLE_COLUMN_MAPS: dict[str, dict[str, str]] = {
    "returns_daily": RETURNS_DAILY_MAP,
    "fs_balance": FS_BALANCE_MAP,
    "fs_income": FS_INCOME_MAP,
    "fs_cashflow": FS_CASHFLOW_MAP,
    "disclosure_dates": DISCLOSURE_DATES_MAP,
    "industry_classification": INDUSTRY_CLASSIFICATION_MAP,
    "top_shareholders": TOP_SHAREHOLDERS_MAP,
    "top_float_shareholders": TOP_FLOAT_SHAREHOLDERS_MAP,
    "inst_holdings": INST_HOLDINGS_MAP,
    "analyst_forecast": ANALYST_FORECAST_MAP,
    "analyst_rating": ANALYST_RATING_MAP,
    "analyst_target_price": ANALYST_TARGET_PRICE_MAP,
}

_NO_DOWNLOADED_AT_TABLES = frozenset({"returns_daily"})

# 标准表契约：必要列与主键

PRICES_DAILY_REQUIRED = ["instrument_id", "date", "open", "high", "low", "close"]
INSTRUMENTS_REQUIRED = ["instrument_id"]

TABLE_REQUIRED_COLUMNS: dict[str, list[str]] = {
    "index_basic": ["index_code", "name", "start_date"],
    "index_daily": ["index_code", "date", "close", "daily_return"],
    "prices_daily": PRICES_DAILY_REQUIRED,
    "returns_daily": ["instrument_id", "date"],
    "instruments": INSTRUMENTS_REQUIRED,
    "fs_balance": ["instrument_id", "report_period", "statement_type"],
    "fs_income": ["instrument_id", "report_period", "statement_type"],
    "fs_cashflow": ["instrument_id", "report_period", "statement_type"],
    "disclosure_dates": ["instrument_id", "report_period", "report_type", "ann_date"],
    "industry_classification": [
        "instrument_id",
        "classification_id",
        "industry_code",
        "effective_date",
    ],
    "top_shareholders": [
        "instrument_id",
        "report_period",
        "shareholder_name",
        "rank",
        "shares_held",
        "pct_of_total",
    ],
    "top_float_shareholders": [
        "instrument_id",
        "report_period",
        "shareholder_name",
        "rank",
        "shares_held",
        "pct_of_float",
    ],
    "inst_holdings": ["instrument_id", "report_period"],
    "analyst_forecast": [
        "instrument_id",
        "report_id",
        "forecast_period",
        "institution_id",
        "publish_date",
    ],
    "analyst_rating": ["instrument_id", "report_id", "institution_id", "publish_date"],
    "analyst_target_price": [
        "instrument_id",
        "report_id",
        "report_date",
        "price_term",
        "publish_date",
    ],
}

TABLE_PRIMARY_KEYS: dict[str, list[str]] = {
    "index_basic": ["index_code", "name"],
    "index_daily": ["index_code", "date", "name"],
    "prices_daily": ["instrument_id", "date"],
    "returns_daily": ["instrument_id", "date"],
    "instruments": ["instrument_id"],
    "fs_balance": ["instrument_id", "report_period", "statement_type"],
    "fs_income": ["instrument_id", "report_period", "statement_type"],
    "fs_cashflow": ["instrument_id", "report_period", "statement_type"],
    "disclosure_dates": ["instrument_id", "report_period", "report_type"],
    "industry_classification": ["instrument_id", "classification_id", "effective_date"],

    "top_shareholders": ["instrument_id", "report_period", "rank", "shareholder_name"],
    "top_float_shareholders": ["instrument_id", "report_period", "rank", "shareholder_name"],
    "inst_holdings": ["instrument_id", "report_period"],

    "analyst_forecast": ["instrument_id", "source_record_key"],
    "analyst_rating": ["instrument_id", "source_record_key"],
    "analyst_target_price": ["instrument_id", "source_record_key"],
}

_DATE_COLUMNS = {
    "date",
    "report_period",
    "ann_date",
    "effective_date",
    "report_date",
    "forecast_period",
    "publish_date",
}

_NON_NUMERIC_COLUMNS = {
    "instrument_id",
    "statement_type",
    "report_type",
    "trading_status",
    "classification_id",
    "classification_name",
    "industry_code",
    "industry_name",
    "shareholder_name",
    "share_nature",
    "shareholder_nature",
    "source_category_id",
    "source_category",
    "report_id",
    "analyst_id",
    "analyst_name",
    "analyst_key",
    "institution_id",
    "institution_name",
    "accounting_period",
    "forecast_year",
    "rating_raw",
    "rating_standard",
    "rating_change",
    "rating_convention",
    "price_term",
    "publish_date_source",
    "source_record_key",
    "source",
    "downloaded_at",
} | _DATE_COLUMNS

_STATEMENT_TYPE_NORMALIZE = {"A": "consolidated", "B": "parent"}

_VALID_PERIOD_ENDS = ("03-31", "06-30", "09-30", "12-31")

_KEEP_ALL_PERIODS_TABLES = frozenset(
    {"top_shareholders", "top_float_shareholders", "inst_holdings"}
)

class CSMARMapper:

    def to_prices_daily(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        
        df = raw_df.rename(columns=PRICES_DAILY_MAP)

        # 仅保留映射后存在的列
        available = [c for c in PRICES_DAILY_MAP.values() if c in df.columns]
        df = df[available].copy()

        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        df["instrument_id"] = df["instrument_id"].astype(str).str.strip().str.zfill(6)

        # 数值列转为 float
        numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.reset_index(drop=True)

    def to_instruments(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        
        df = raw_df.rename(columns=INSTRUMENTS_MAP)

        if "instrument_id" not in df.columns:
            raise ProviderContractError(
                code="PROVIDER_CONTRACT_MISSING_FIELD",
                message="缺少 instrument_id（Stkcd）字段。",
                blocking=True,
            )

        instruments = (
            df[["instrument_id"]]
            .drop_duplicates()
            .assign(
                instrument_id=lambda x: x["instrument_id"].astype(str).str.strip().str.zfill(6),
                source="csmar",
            )
        )
        return instruments.reset_index(drop=True)

    def to_standard(self, raw_df: pd.DataFrame, staged_name: str) -> pd.DataFrame:
        if staged_name in {"index_basic", "index_daily"}:
            return self._indices(raw_df, staged_name)
        if staged_name == "prices_daily":
            return self.to_prices_daily(raw_df)
        if staged_name == "instruments":
            return self.to_instruments(raw_df)

        col_map = TABLE_COLUMN_MAPS.get(staged_name)
        if col_map is None:
            raise ProviderContractError(
                code="PROVIDER_CONTRACT_UNKNOWN_TABLE",
                message=f"未定义字段映射的表：{staged_name}。",
                context={"known_tables": sorted(TABLE_COLUMN_MAPS)},
                blocking=True,
            )
        return self._map_strict(raw_df, staged_name, col_map)

    def _indices(self, raw_df: pd.DataFrame, name: str) -> pd.DataFrame:
        mapping = ({"Indexcd": "index_code", "Idxinfo01": "name", "Idxinfo02": "base_date",
                    "Idxinfo03": "base_value", "Idxinfo04": "scope", "Idxinfo05": "weighting",
                    "Idxinfo06": "publisher", "Idxinfo07": "asset_type", "Idxinfo08": "index_type",
                    "Idxinfo09": "market", "Idxinfo10": "formula", "Idxinfo11": "start_date"}
                   if name == "index_basic" else
                   {"Indexcd": "index_code", "Idxtrd01": "date", "Idxtrd02": "open", "Idxtrd03": "high",
                    "Idxtrd04": "low", "Idxtrd05": "close", "Idxtrd06": "volume_10k",
                    "Idxtrd07": "amount_10k", "Idxtrd08": "return_pct", "Idxtrd09": "name"})
        unknown = set(raw_df.columns) - mapping.keys()
        if unknown:
            raise ProviderContractError(code="PROVIDER_CONTRACT_UNKNOWN_FIELD", message=f"{name} 未知字段：{sorted(unknown)}")
        frame = raw_df.rename(columns=mapping).copy()
        frame["index_code"] = frame["index_code"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
        numeric = {"base_value", "index_type", "open", "high", "low", "close", "volume_10k", "amount_10k", "return_pct"}
        for column in frame:
            if column in {"date", "base_date", "start_date"}:
                frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.strftime("%Y-%m-%d")
            elif column in numeric:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            else:
                frame[column] = frame[column].astype("string")
                if column in {"market", "publisher", "asset_type", "weighting"}:
                    frame[column] = frame[column].str.replace(r"\.0$", "", regex=True)
        if name == "index_daily":
            frame["daily_return"] = frame.pop("return_pct") / 100
        frame["source"] = "csmar"
        return frame.reset_index(drop=True)

    def _map_strict(
        self,
        raw_df: pd.DataFrame,
        staged_name: str,
        col_map: dict[str, str],
    ) -> pd.DataFrame:
        
        unknown = [c for c in raw_df.columns if c not in col_map]
        if unknown:
            raise ProviderContractError(
                code="PROVIDER_CONTRACT_UNKNOWN_FIELD",
                message=(
                    f"表 {staged_name} 下载结果包含未在映射表中声明的字段：{unknown}。"
                    "请核对 CSMAR 数据字典后更新 mapper.py 与 tables.py。"
                ),
                context={"staged_name": staged_name, "unknown_fields": unknown},
                blocking=True,
                suggested_action=(
                    "在 mapper.py 的对应字段映射与 tables.py 的字段清单中补充或修正字段后重试。"
                ),
            )

        df = raw_df.rename(columns=col_map).copy()

        raw_codes = (
            df["instrument_id"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
        )
        excluded_empty_artifacts = 0
        if staged_name.startswith("analyst_"):

            business_columns = [col for col in df.columns if col != "instrument_id"]
            report_id_blank = (
                df["report_id"].isna() | df["report_id"].astype("string").str.strip().eq("")
            ).fillna(True)
            artifact_candidate_mask = (
                raw_codes.str.fullmatch(r"\d{1,6}", na=False) & report_id_blank
            )
            empty_artifact_mask = pd.Series(False, index=df.index, dtype="bool")
            if artifact_candidate_mask.any():
                candidate_business = df.loc[artifact_candidate_mask, business_columns]
                business_blank = (
                    candidate_business.isna()
                    | candidate_business.astype("string").apply(
                        lambda column: column.str.strip().eq("")
                    )
                ).fillna(True)
                empty_artifact_mask.loc[artifact_candidate_mask] = business_blank.all(axis=1)
            excluded_empty_artifacts = int(empty_artifact_mask.sum())
            if excluded_empty_artifacts:
                df = df.loc[~empty_artifact_mask].copy()
                raw_codes = raw_codes.loc[~empty_artifact_mask]

            valid_code = raw_codes.str.fullmatch(r"\d{1,6}", na=False)
            df = df.loc[valid_code].copy()
            raw_codes = raw_codes.loc[valid_code]
        df["instrument_id"] = raw_codes.str.zfill(6)

        for col in ("report_id", "institution_id", "analyst_id"):
            if col in df.columns:
                numeric = pd.to_numeric(df[col], errors="coerce")
                normalized = numeric.map(lambda value: f"{value:.0f}" if pd.notna(value) else pd.NA)
                original = df[col].astype("string").str.strip().replace("", pd.NA)
                df[col] = normalized.fillna(original).astype("string")

        for col in df.columns:
            if col in _DATE_COLUMNS:

                df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
            elif col not in _NON_NUMERIC_COLUMNS:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "statement_type" in df.columns:
            df["statement_type"] = self._normalize_statement_type(df["statement_type"])

        if staged_name in {"analyst_forecast", "analyst_rating"}:
            forecast_year = df["forecast_period"].str[:4]
            if "accounting_period" in df.columns:
                accounting_year = (
                    df["accounting_period"].astype("string").str.extract(r"(\d{4})", expand=False)
                )
                forecast_year = forecast_year.fillna(accounting_year)
            df["forecast_year"] = forecast_year.astype("string")

            df["analyst_key"] = (
                df["analyst_id"].fillna(df["analyst_name"]).fillna("").astype("string")
            )

        if staged_name.startswith("analyst_"):

            identity_columns = {
                "analyst_forecast": [
                    "report_id",
                    "report_date",
                    "forecast_period",
                    "institution_id",
                    "analyst_key",
                ],
                "analyst_rating": [
                    "report_id",
                    "report_date",
                    "forecast_period",
                    "institution_id",
                    "analyst_key",
                ],
                "analyst_target_price": ["report_id", "report_date", "price_term"],
            }[staged_name]
            identity = df[["instrument_id", *identity_columns]].astype("string").fillna("<NA>")
            df["source_record_key"] = (
                pd.util.hash_pandas_object(identity, index=False).astype("uint64").astype("string")
            )

        if "report_period" in df.columns and staged_name not in _KEEP_ALL_PERIODS_TABLES:
            df = df[df["report_period"].str[5:].isin(_VALID_PERIOD_ENDS)]

        if staged_name not in _NO_DOWNLOADED_AT_TABLES:
            df["downloaded_at"] = datetime.now(tz=UTC).isoformat()

        result = df.reset_index(drop=True)
        result.attrs["excluded_empty_export_artifacts"] = excluded_empty_artifacts
        return result

    def _normalize_statement_type(self, series: pd.Series) -> pd.Series:
        
        cleaned = series.astype(str).str.strip()
        invalid = sorted(set(cleaned) - set(_STATEMENT_TYPE_NORMALIZE))
        if invalid:
            raise ProviderContractError(
                code="PROVIDER_CONTRACT_INVALID_STATEMENT_TYPE",
                message=f"报表类型（Typrep）出现未知取值：{invalid}（已知 A=合并, B=母公司）。",
                context={"invalid_values": invalid},
                blocking=True,
            )
        return cleaned.map(_STATEMENT_TYPE_NORMALIZE)

    def validate_schema(self, df: pd.DataFrame, table_name: str) -> list[str]:
        
        errors: list[str] = []

        required = TABLE_REQUIRED_COLUMNS.get(table_name)
        if required is None:
            errors.append(f"未知表名：{table_name}")
            return errors

        missing = [c for c in required if c not in df.columns]
        if missing:
            errors.append(f"缺少必要列：{missing}")
            return errors

        pk = TABLE_PRIMARY_KEYS[table_name]

        for col in pk:
            if df[col].isna().any():
                errors.append(f"{col} 存在空值。")

        if errors:
            return errors

        dup = df.duplicated(subset=pk)
        if dup.any():
            n = int(dup.sum())
            errors.append(f"主键 {tuple(pk)} 存在 {n} 条重复。")

        return errors
