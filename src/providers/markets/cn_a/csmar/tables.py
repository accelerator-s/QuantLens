from __future__ import annotations

from dataclasses import dataclass

from providers.exceptions import ProviderDataError

SIGN_NORMAL = "0"
SIGN_TIME_KEY = "1"
SIGN_CODE_KEY = "2"

@dataclass(frozen=True)
class CSMARFieldDef:

    field: str
    title: str
    fid: int | None = None
    type: str = "decimal"
    sign: str = SIGN_NORMAL

@dataclass(frozen=True)
class CSMARTableConfig:

    staged_name: str
    table_name: str
    table_name_phy: str
    table_id: int | None
    code_set_field: str
    time_set_field: str
    fields: tuple[CSMARFieldDef, ...]
    batch_freq: str  

    @property
    def verified(self) -> bool:
        
        return self.table_id is not None and all(f.fid is not None for f in self.fields)

    def to_client_fields(self) -> list[dict]:
        
        require_verified(self)
        return [
            {"id": f.fid, "field": f.field, "title": f.title, "type": f.type, "sign": f.sign}
            for f in self.fields
        ]

def require_verified(cfg: CSMARTableConfig) -> None:
    
    if cfg.verified:
        return
    pending = [f.field for f in cfg.fields if f.fid is None]
    raise ProviderDataError(
        code="PROVIDER_TABLE_UNVERIFIED",
        message=(
            f"表 {cfg.staged_name}（{cfg.table_name_phy}）的 CSMAR 下载参数尚未核对："
            f"table_id={cfg.table_id}，待核对字段 id：{pending or '无'}。"
        ),
        context={
            "staged_name": cfg.staged_name,
            "table_name_phy": cfg.table_name_phy,
            "table_id": cfg.table_id,
            "pending_fields": pending,
        },
        blocking=True,
        suggested_action=(
            "登录 data.csmar.com，在目标表下载页勾选字段后用浏览器开发者工具"
            "抓取 saveOutline 请求，核对 tableId / tableNamePhy / 各字段数值 id，"
            "填入 src/providers/markets/cn_a/csmar/tables.py 后重试。"
        ),
    )

_PRICES_DAILY = CSMARTableConfig(
    staged_name="prices_daily",
    table_name="日个股回报率文件",
    table_name_phy="TRD_Dalyr",
    table_id=4,
    code_set_field="Stkcd",
    time_set_field="Trddt",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 58, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Trddt", "交易日期", 59, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Opnprc", "日开盘价", 60),
        CSMARFieldDef("Hiprc", "日最高价", 61),
        CSMARFieldDef("Loprc", "日最低价", 62),
        CSMARFieldDef("Clsprc", "日收盘价", 63),
        CSMARFieldDef("Dnshrtrd", "日个股交易股数", 64),
        CSMARFieldDef("Dnvaltrd", "日个股交易金额", 65),
    ),
    batch_freq="quarter",
)

_FS_BALANCE = CSMARTableConfig(
    staged_name="fs_balance",
    table_name="资产负债表",
    table_name_phy="FS_Combas",
    table_id=224,
    code_set_field="Stkcd",
    time_set_field="Accper",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 3394, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Accper", "统计截止日期", 3395, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Typrep", "报表类型", 3396, "Nvarchar"),
        CSMARFieldDef("A001101000", "货币资金", 3397),
        CSMARFieldDef("A001111000", "应收账款净额", 3409),
        CSMARFieldDef("A001123000", "存货净额", 3423),
        CSMARFieldDef("A001100000", "流动资产合计", 3427),
        CSMARFieldDef("A001212000", "固定资产净额", 3440),
        CSMARFieldDef("A001000000", "资产总计", 3455),
        CSMARFieldDef("A002101000", "短期借款", 3456),
        CSMARFieldDef("A002100000", "流动负债合计", 3490),
        CSMARFieldDef("A002201000", "长期借款", 3491),
        CSMARFieldDef("A002000000", "负债合计", 3503),
        CSMARFieldDef("A003100000", "归属于母公司所有者权益合计", 3519),
        CSMARFieldDef("A003000000", "所有者权益合计", 3521),
    ),
    batch_freq="year",
)

_FS_INCOME = CSMARTableConfig(
    staged_name="fs_income",
    table_name="利润表",
    table_name_phy="FS_Comins",
    table_id=225,
    code_set_field="Stkcd",
    time_set_field="Accper",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 3523, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Accper", "统计截止日期", 3524, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Typrep", "报表类型", 3525, "Nvarchar"),
        CSMARFieldDef("B001100000", "营业总收入", 3526),
        CSMARFieldDef("B001101000", "营业收入", 3527),
        CSMARFieldDef("B001200000", "营业总成本", 3543),
        CSMARFieldDef("B001201000", "营业成本", 3544),
        CSMARFieldDef("B001300000", "营业利润", 3567),
        CSMARFieldDef("B001000000", "利润总额", 3573),
        CSMARFieldDef("B002000000", "净利润", 3577),
        CSMARFieldDef("B002000101", "归属于母公司所有者的净利润", 3578),
    ),
    batch_freq="year",
)

_FS_CASHFLOW = CSMARTableConfig(
    staged_name="fs_cashflow",
    table_name="现金流量表(直接法)",
    table_name_phy="FS_Comscfd",
    table_id=226,
    code_set_field="Stkcd",
    time_set_field="Accper",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 3586, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Accper", "统计截止日期", 3587, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Typrep", "报表类型", 3588, "Nvarchar"),
        CSMARFieldDef("C001000000", "经营活动产生的现金流量净额", 3611),
        CSMARFieldDef("C002000000", "投资活动产生的现金流量净额", 3622),
        CSMARFieldDef("C002006000", "购建固定资产无形资产和其他长期资产支付的现金", 3617),
        CSMARFieldDef("C003000000", "筹资活动产生的现金流量净额", 3633),
        CSMARFieldDef("C005000000", "现金及现金等价物净增加额", 3636),
    ),
    batch_freq="year",
)

_DISCLOSURE_DATES = CSMARTableConfig(
    staged_name="disclosure_dates",
    table_name="年、中、季报基本情况文件",
    table_name_phy="IAR_Rept",
    table_id=229,
    code_set_field="Stkcd",
    time_set_field="Accper",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 3717, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Accper", "统计截止日期", 3721, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Reptyp", "报告类型", 3720, "decimal"),
        CSMARFieldDef("Annodt", "报告公布日期", 3722, "Datetime"),
    ),
    batch_freq="year",
)

_INDUSTRY_CLASSIFICATION = CSMARTableConfig(
    staged_name="industry_classification",
    table_name="上市公司行业分类表",
    table_name_phy="STK_INDUSTRYCLASS",
    table_id=564,
    code_set_field="Symbol",
    time_set_field="ImplementDate",
    fields=(
        CSMARFieldDef("Symbol", "股票代码", 10538, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("IndustryClassificationID", "行业分类标准编码", 10539, "Nvarchar"),
        CSMARFieldDef("IndustryClassificationName", "行业分类标准", 10540, "Nvarchar"),
        CSMARFieldDef("ImplementDate", "实施开始日期", 10541, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("IndustryCode", "行业代码", 10542, "Nvarchar"),
        CSMARFieldDef("IndustryName", "行业名称", 10543, "Nvarchar"),
    ),
    batch_freq="year",
)

_RETURNS_DAILY = CSMARTableConfig(
    staged_name="returns_daily",
    table_name="日个股回报率文件（复权/市值/交易状态）",
    table_name_phy="TRD_Dalyr",
    table_id=4,
    code_set_field="Stkcd",
    time_set_field="Trddt",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 58, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Trddt", "交易日期", 59, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Dretwd", "考虑现金红利再投资的日个股回报率", 68),
        CSMARFieldDef("Dretnd", "不考虑现金红利的日个股回报率", 69),
        CSMARFieldDef("Adjprcwd", "考虑现金红利再投资的收盘价的可比价格", 70),
        CSMARFieldDef("Adjprcnd", "不考虑现金红利的收盘价的可比价格", 71),
        CSMARFieldDef("Dsmvosd", "日个股流通市值（千元）", 66),
        CSMARFieldDef("Dsmvtll", "日个股总市值（千元）", 67),
        CSMARFieldDef("Trdsta", "交易状态", 74, "Nvarchar"),
    ),
    batch_freq="quarter",
)

_TOP_SHAREHOLDERS = CSMARTableConfig(
    staged_name="top_shareholders",
    table_name="十大股东文件",
    table_name_phy="HLD_Shareholders",
    table_id=352,
    code_set_field="Stkcd",
    time_set_field="Reptdt",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 6289, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Reptdt", "统计截止日期", 6290, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("S0301a", "股东名称", 6291, "Nvarchar"),
        CSMARFieldDef("S0306a", "持股排名", 6296, "decimal"),
        CSMARFieldDef("S0302a", "持股数量", 6292),
        CSMARFieldDef("S0304a", "持股比例(%)", 6294),
        CSMARFieldDef("S0305a", "股份性质", 6295, "Nvarchar"),
        CSMARFieldDef("ShareholderNature", "股东性质", 90775, "Nvarchar"),
        CSMARFieldDef("CategoryID", "股东类型编码", 91956, "Nvarchar"),
        CSMARFieldDef("Category", "股东类型", 91957, "Nvarchar"),
    ),
    batch_freq="year",
)

_TOP_FLOAT_SHAREHOLDERS = CSMARTableConfig(
    staged_name="top_float_shareholders",
    table_name="十大流通股股东文件",
    table_name_phy="HLD_Negshr",
    table_id=354,
    code_set_field="Stkcd",
    time_set_field="Reptdt",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 6305, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Reptdt", "统计截止日期", 6306, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("S0401a", "股东名称", 6307, "Nvarchar"),
        CSMARFieldDef("S0404a", "持股排名", 6310, "decimal"),
        CSMARFieldDef("S0402a", "持股数量", 6308),
        CSMARFieldDef("ShareholdingRatio", "持股比例", 109976),
        CSMARFieldDef("SharesTradSharesRatio", "持股数占流通股总股数比例", 109977),
        CSMARFieldDef("SharesTradASharesRatio", "持股数占流通A股数比例", 135906),
        CSMARFieldDef("S0403a", "股份性质", 6309, "Nvarchar"),
    ),
    batch_freq="year",
)

_INST_HOLDINGS = CSMARTableConfig(
    staged_name="inst_holdings",
    table_name="机构持股分类统计表",
    table_name_phy="INI_HolderSystematics",
    table_id=431,
    code_set_field="Symbol",
    time_set_field="EndDate",
    fields=(
        CSMARFieldDef("Symbol", "股票代码", 7747, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("EndDate", "截止日期", 7748, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("FundHoldShares", "基金持股数量", 7749),
        CSMARFieldDef("FundHoldProportion", "基金持股比例(相对总股本)", 7750),
        CSMARFieldDef("FundHoldProportion1", "基金持股比例(相对流通A股)", 135913),
        CSMARFieldDef("QFIIHoldShares", "合格境外投资者持股数量", 7751),
        CSMARFieldDef("QFIIHoldProportion", "合格境外投资者持股比例(相对总股本)", 7752),
        CSMARFieldDef("QFIIHoldProportion1", "合格境外投资者持股比例(相对流通A股)", 135914),
        CSMARFieldDef("BrokerHoldShares", "券商持股数量", 7753),
        CSMARFieldDef("BrokerHoldProportion", "券商持股比例(相对总股本)", 7754),
        CSMARFieldDef("BrokerHoldProportion1", "券商持股比例(相对流通A股)", 135915),
        CSMARFieldDef("InsuranceHoldShares", "保险持股数量", 7755),
        CSMARFieldDef("InsuranceHoldProportion", "保险持股比例(相对总股本)", 7756),
        CSMARFieldDef("InsuranceHoldProportion1", "保险持股比例(相对流通A股)", 135916),
        CSMARFieldDef("SecurityFundHoldShares", "社保基金持股数量", 7757),
        CSMARFieldDef("SecurityFundHoldProportion", "社保基金持股比例(相对总股本)", 7758),
        
        CSMARFieldDef("SecurityFundHProportion1", "社保基金持股比例(相对流通A股)", 135917),
        CSMARFieldDef("EntrustHoldShares", "信托持股数量", 7759),
        CSMARFieldDef("EntrustHoldProportion", "信托持股比例(相对总股本)", 7760),
        CSMARFieldDef("EntrustHoldProportion1", "信托持股比例(相对流通A股)", 135918),
        CSMARFieldDef("FinanceHoldShares", "财务公司持股数量", 7761),
        CSMARFieldDef("FinanceHoldProportion", "财务公司持股比例(相对总股本)", 7762),
        CSMARFieldDef("FinanceHoldProportion1", "财务公司持股比例(相对流通A股)", 135919),
        CSMARFieldDef("BankHoldShares", "银行持股数量", 7763),
        CSMARFieldDef("BankHoldProportion", "银行持股比例(相对总股本)", 7764),
        CSMARFieldDef("BankHoldProportion1", "银行持股比例(相对流通A股)", 135920),
        CSMARFieldDef("NonFinanceHoldShares", "非金融类上市公司持股数量", 7765),
        CSMARFieldDef("NonFinanceHoldProportion", "非金融类上市公司持股比例(相对总股本)", 7766),
        CSMARFieldDef("NonFinanceHoldProportion1", "非金融类上市公司持股比例(相对流通A股)", 135921),
        CSMARFieldDef("OtherHoldShares", "其他机构持股数量", 90801),
        CSMARFieldDef("OtherHoldProportion", "其他机构持股比例(相对总股本)", 90802),
        CSMARFieldDef("OtherHoldProportion1", "其他机构持股比例(相对流通A股)", 135922),
        CSMARFieldDef("TotalHoldShares", "机构投资者持股数量", 109828),
        CSMARFieldDef("InsInvestorProp", "机构投资者持股比例(相对总股本)", 109829),
        CSMARFieldDef("InsInvestorProp1", "机构投资者持股比例(相对流通A股)", 135923),
    ),
    batch_freq="year",
)

_ANALYST_FORECAST = CSMARTableConfig(
    staged_name="analyst_forecast",
    table_name="分析师预测指标文件",
    table_name_phy="AF_Forecast",
    table_id=299,
    code_set_field="Stkcd",
    time_set_field="Rptdt",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 4979, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Rptdt", "报告撰写日期", 4980, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Fenddt", "预测终止日期", 4981, "Datetime"),
        CSMARFieldDef("ReportID", "研究报告编码", 4984, "decimal"),
        CSMARFieldDef("DeclareDate", "平台发布日期", 110120, "Nvarchar"),
        CSMARFieldDef("AnanmID", "分析师编码", 4982, "Nvarchar"),
        CSMARFieldDef("Ananm", "分析师姓名", 4983, "Nvarchar"),
        CSMARFieldDef("InstitutionID", "机构编码", 4985, "decimal"),
        CSMARFieldDef("Brokern", "机构名称", 4986, "Nvarchar"),
        CSMARFieldDef("Feps", "预测每股收益", 4987),
        CSMARFieldDef("Fpe", "预测市盈率", 4988),
        CSMARFieldDef("Fnetpro", "预测净利润", 4989),
        CSMARFieldDef("Febit", "预测息税前利润", 4990),
        CSMARFieldDef("Febitda", "预测息税折旧摊销前利润", 4991),
        CSMARFieldDef("Fturnover", "预测营业收入", 4992),
        CSMARFieldDef("Fcfps", "预测每股现金流", 4993),
        CSMARFieldDef("FBPS", "预测每股净资产", 4994),
        CSMARFieldDef("FROA", "预测总资产收益率", 4995),
        CSMARFieldDef("FROE", "预测净资产收益率", 4996),
        CSMARFieldDef("FPB", "预测市净率", 4997),
        CSMARFieldDef("FTotalAssetsTurnover", "预测总资产周转率", 4998),
        CSMARFieldDef("ProfitParent", "预测归母净利润", 133226),
        CSMARFieldDef("TotalProfit", "预测利润总额", 133227),
        CSMARFieldDef("OperationProfit", "预测营业利润", 133228),
        CSMARFieldDef("TotalAsset", "预测总资产", 133229),
        CSMARFieldDef("TotalLiabilities", "预测总负债", 133230),
    ),
    batch_freq="year",
)

_ANALYST_RATING = CSMARTableConfig(
    staged_name="analyst_rating",
    table_name="分析师评级及变动",
    table_name_phy="AF_Bench",
    table_id=300,
    code_set_field="Stkcd",
    time_set_field="Rptdt",
    fields=(
        CSMARFieldDef("Stkcd", "证券代码", 4999, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("ReportID", "研究报告编码", 5000, "decimal"),
        CSMARFieldDef("Accper", "会计年度", 5001, "Nvarchar"),
        CSMARFieldDef("Rptdt", "报告撰写日期", 5002, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Fenddt", "预测终止日期", 5003, "Nvarchar"),
        CSMARFieldDef("Ananm", "分析师姓名", 5005, "Nvarchar"),
        CSMARFieldDef("DeclareDate", "平台发布日期", 110121, "Nvarchar"),
        CSMARFieldDef("AnanmID", "分析师编码", 5004, "Nvarchar"),
        CSMARFieldDef("InstitutionID", "机构编码", 5006, "decimal"),
        CSMARFieldDef("Brokern", "机构名称", 5007, "Nvarchar"),
        CSMARFieldDef("Investrank", "原始投资评级", 5008, "Nvarchar"),
        CSMARFieldDef("Stdrank", "CSMAR 标准评级", 5009, "Nvarchar"),
        CSMARFieldDef("Rankchg", "评级变动", 5010, "Nvarchar"),
        CSMARFieldDef("Rankbench", "评级口径说明", 5011, "Ntext"),
        CSMARFieldDef("ForecastAccuracy", "预测准确度", 5012),
        CSMARFieldDef("ForecastOptimism", "预测乐观度", 5013),
        CSMARFieldDef("RivisionHorizon", "预测修正区间", 5014),
        CSMARFieldDef("ForecastHorizon", "预测区间", 5015),
    ),
    batch_freq="year",
)

_ANALYST_TARGET_PRICE = CSMARTableConfig(
    staged_name="analyst_target_price",
    table_name="目标价格表",
    table_name_phy="AF_TARGETVALUE",
    table_id=301,
    code_set_field="Stkcd",
    time_set_field="ReportDate",
    fields=(
        CSMARFieldDef("ReportID", "研究报告编码", 5016, "decimal"),
        CSMARFieldDef("Stkcd", "证券代码", 5017, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("ReportDate", "报告撰写日期", 5018, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("PriceTerm", "目标价期限", 5021, "Nvarchar"),
        CSMARFieldDef("DeclareDate", "平台发布日期", 110122, "Nvarchar"),
        CSMARFieldDef("ObjectPriceMax", "目标价上限", 5019),
        CSMARFieldDef("ObjectPriceMin", "目标价下限", 5020),
    ),
    batch_freq="year",
)

_INDEX_BASIC = CSMARTableConfig(
    staged_name="index_basic", table_name="指数基本信息文件", table_name_phy="IDX_Idxinfo",
    table_id=40, code_set_field="Indexcd", time_set_field="Idxinfo11", batch_freq="snapshot",
    fields=(
        CSMARFieldDef("Indexcd", "交易所指数代码", 648, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Idxinfo01", "指数名称", 649, "Nvarchar"),
        CSMARFieldDef("Idxinfo02", "基准日期", 650, "Datetime"),
        CSMARFieldDef("Idxinfo03", "基准点数", 651),
        CSMARFieldDef("Idxinfo05", "加权方式", 653, "Nvarchar"),
        CSMARFieldDef("Idxinfo06", "发布机构", 654, "Nvarchar"),
        CSMARFieldDef("Idxinfo07", "指数类别", 655, "Nvarchar"),
        CSMARFieldDef("Idxinfo08", "指数类型", 656),
        CSMARFieldDef("Idxinfo09", "所属市场", 657, "Nvarchar"),
        CSMARFieldDef("Idxinfo11", "开始日期", 659, "Datetime", SIGN_TIME_KEY),
    ),
)

_INDEX_DAILY = CSMARTableConfig(
    staged_name="index_daily", table_name="国内指数日行情文件", table_name_phy="IDX_Idxtrd",
    table_id=45, code_set_field="Indexcd", time_set_field="Idxtrd01", batch_freq="year",
    fields=(
        CSMARFieldDef("Indexcd", "交易所指数代码", 691, "Nvarchar", SIGN_CODE_KEY),
        CSMARFieldDef("Idxtrd01", "交易日期", 692, "Datetime", SIGN_TIME_KEY),
        CSMARFieldDef("Idxtrd02", "开盘指数", 693),
        CSMARFieldDef("Idxtrd03", "最高指数", 694),
        CSMARFieldDef("Idxtrd04", "最低指数", 695),
        CSMARFieldDef("Idxtrd05", "收盘指数", 696),
        CSMARFieldDef("Idxtrd06", "成份证券成交量（万股）", 697),
        CSMARFieldDef("Idxtrd07", "成份证券成交金额（万元）", 698),
        CSMARFieldDef("Idxtrd08", "指数回报率（%）", 699),
        CSMARFieldDef("Idxtrd09", "指数中文简称", 150943, "Nvarchar"),
    ),
)

TABLE_REGISTRY: dict[str, CSMARTableConfig] = {
    cfg.staged_name: cfg
    for cfg in (
        _PRICES_DAILY,
        _RETURNS_DAILY,
        _FS_BALANCE,
        _FS_INCOME,
        _FS_CASHFLOW,
        _DISCLOSURE_DATES,
        _INDUSTRY_CLASSIFICATION,
        _TOP_SHAREHOLDERS,
        _TOP_FLOAT_SHAREHOLDERS,
        _INST_HOLDINGS,
        _ANALYST_FORECAST,
        _ANALYST_RATING,
        _ANALYST_TARGET_PRICE,
        _INDEX_BASIC,
        _INDEX_DAILY,
    )
}

FUNDAMENTAL_TABLES: tuple[str, ...] = ("fs_balance", "fs_income", "fs_cashflow")

HOLDER_TABLES: tuple[str, ...] = ("top_shareholders", "top_float_shareholders", "inst_holdings")

ANALYST_TABLES: tuple[str, ...] = (
    "analyst_forecast",
    "analyst_rating",
    "analyst_target_price",
)

def get_table_config(staged_name: str) -> CSMARTableConfig:
    
    cfg = TABLE_REGISTRY.get(staged_name)
    if cfg is None:
        raise ProviderDataError(
            code="PROVIDER_UNKNOWN_TABLE",
            message=f"未知的 staged 表名：{staged_name}。",
            context={"known_tables": sorted(TABLE_REGISTRY)},
            blocking=True,
            suggested_action=f"可用表：{', '.join(sorted(TABLE_REGISTRY))}。",
        )
    return cfg

def table_summaries() -> list[dict]:
    
    return [
        {
            "staged_name": cfg.staged_name,
            "table_name": cfg.table_name,
            "table_name_phy": cfg.table_name_phy,
            "table_id": cfg.table_id,
            "fields": len(cfg.fields),
            "batch_freq": cfg.batch_freq,
            "verified": cfg.verified,
        }
        for cfg in TABLE_REGISTRY.values()
    ]
