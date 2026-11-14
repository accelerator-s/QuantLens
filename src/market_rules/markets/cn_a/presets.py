from __future__ import annotations

from datetime import time
from decimal import Decimal

from market_rules.models import (
    BoardQuantityRule,
    DisclosureDeadlineRule,
    DisclosureDeadlineSchedule,
    FeeItem,
    FeeSchedule,
    MarketRuleConfig,
    MarketRuleStatus,
    PriceLimitRule,
    PriceLimitSpecialRule,
    SessionDefinition,
    SessionType,
    TaxItem,
    TaxSchedule,
    TradeDirection,
)

PRESET_NAME = "CN-A-stock"

def build_cn_a_stock() -> MarketRuleConfig:
    
    return MarketRuleConfig(
        name=PRESET_NAME,
        market="CN-A",
        instrument_class="stock",
        calendar_source="上交所/深交所官方休市安排",
        timezone="Asia/Shanghai",
        sessions=[
            SessionDefinition(
                name="集合竞价（开盘）",
                start=time(9, 15),
                end=time(9, 25),
                session_type=SessionType.CALL_AUCTION,
                notes="9:15-9:20 可撤单，9:20-9:25 不可撤单",
            ),
            SessionDefinition(
                name="连续竞价（上午）",
                start=time(9, 30),
                end=time(11, 30),
                session_type=SessionType.CONTINUOUS,
            ),
            SessionDefinition(
                name="连续竞价（下午）",
                start=time(13, 0),
                end=time(14, 57),
                session_type=SessionType.CONTINUOUS,
            ),
            SessionDefinition(
                name="集合竞价（收盘）",
                start=time(14, 57),
                end=time(15, 0),
                session_type=SessionType.CLOSING_AUCTION,
                notes="收盘集合竞价阶段不接受撤单申报",
            ),
        ],
        session_anchor="交易日以自然日为基准，夜盘归属下一交易日（A 股无夜盘）",
        night_session=False,
        holidays_source="国务院办公厅节假日安排 + 交易所休市公告",
        t_plus=1,
        min_unit=100,
        fractional_shares=False,
        quantity_rules=[
            BoardQuantityRule(
                board="主板",
                min_unit=100,
                increment=0,
                notes="沪深主板：100 股整手交易",
            ),
            BoardQuantityRule(
                board="科创板",
                min_unit=200,
                increment=1,
                notes="科创板：每笔至少 200 股，超出可 1 股递增",
            ),
            BoardQuantityRule(
                board="创业板",
                min_unit=100,
                increment=0,
                notes="创业板：100 股整手交易",
            ),
            BoardQuantityRule(
                board="北交所",
                min_unit=100,
                increment=1,
                notes="北交所：每笔至少 100 股，超出可 1 股递增",
            ),
        ],
        price_limit=PriceLimitRule(
            enabled=True,
            default_pct=Decimal("10"),
            special_rules=[
                PriceLimitSpecialRule(
                    condition="ST",
                    limit_pct=Decimal("5"),
                    notes="ST 及 *ST 股票涨跌幅限制为 5%",
                ),
                PriceLimitSpecialRule(
                    condition="科创板",
                    limit_pct=Decimal("20"),
                    notes="科创板（688 开头）涨跌幅限制为 20%",
                ),
                PriceLimitSpecialRule(
                    condition="创业板",
                    limit_pct=Decimal("20"),
                    notes="创业板（300 开头）涨跌幅限制为 20%（2020年8月24日起）",
                ),
                PriceLimitSpecialRule(
                    condition="北交所",
                    limit_pct=Decimal("30"),
                    notes="北交所涨跌幅限制为 30%",
                ),
                PriceLimitSpecialRule(
                    condition="科创板ST",
                    limit_pct=Decimal("20"),
                    notes="科创板 ST 股票涨跌幅不变，仍为 ±20%",
                ),
                PriceLimitSpecialRule(
                    condition="创业板ST",
                    limit_pct=Decimal("20"),
                    notes="创业板 ST 股票涨跌幅不变，仍为 ±20%",
                ),
                PriceLimitSpecialRule(
                    condition="北交所ST",
                    limit_pct=Decimal("30"),
                    notes="北交所 ST 股票涨跌幅不变，仍为 ±30%",
                ),
            ],
            first_day_rule=(
                "全面注册制（2023年2月起）：沪深主板/科创板/创业板新股上市前 5 个交易日"
                "不设涨跌幅限制；北交所新股上市首日不设涨跌幅限制，次日起执行 ±30%。"
                "上市首日设临时停牌机制（科创板/创业板/北交所：盘中涨跌达 ±30% 停牌 10 分钟，"
                "达 ±60% 停牌至 14:57）。"
            ),
            notes="涨跌停价格 = 前收盘价 × (1 ± 涨跌幅比例)，精确到分（四舍五入）",
        ),
        suspension_handling=(
            "停牌期间不计入交易日；复牌首日按正常涨跌幅限制交易（除特殊规定外）。"
            "科创板/创业板/北交所盘中临时停牌：涨跌达 ±30% 停牌 10 分钟，"
            "达 ±60% 停至当日 14:57 复牌。"
        ),
        fees=FeeSchedule(
            items=[
                FeeItem(
                    name="佣金",
                    rate=Decimal("0.00008"),
                    min_amount=Decimal("5"),
                    direction=TradeDirection.BOTH,
                    notes="万0.8，单笔最低 5 元",
                ),
                FeeItem(
                    name="过户费",
                    rate=Decimal("0.00001"),
                    direction=TradeDirection.BOTH,
                    notes="万分之零点一（2022年4月29日起沪深统一）",
                ),
            ],
        ),
        taxes=TaxSchedule(
            items=[
                TaxItem(
                    name="印花税",
                    rate=Decimal("0.0005"),
                    direction=TradeDirection.SELL,
                    notes="千分之零点五（2023年8月28日起减半征收），卖出时单边收取",
                ),
            ],
            notes="财政部、税务总局 2023 年第 39 号公告：印花税减半",
        ),
        disclosure_deadlines=DisclosureDeadlineSchedule(
            items=[
                DisclosureDeadlineRule(
                    period_end="03-31",
                    deadline="04-30",
                    year_offset=0,
                    notes="一季报：报告期结束后 1 个月内（当年 4 月 30 日前）披露",
                ),
                DisclosureDeadlineRule(
                    period_end="06-30",
                    deadline="08-31",
                    year_offset=0,
                    notes="半年报：报告期结束后 2 个月内（当年 8 月 31 日前）披露",
                ),
                DisclosureDeadlineRule(
                    period_end="09-30",
                    deadline="10-31",
                    year_offset=0,
                    notes="三季报：报告期结束后 1 个月内（当年 10 月 31 日前）披露",
                ),
                DisclosureDeadlineRule(
                    period_end="12-31",
                    deadline="04-30",
                    year_offset=1,
                    notes="年报：会计年度结束后 4 个月内（次年 4 月 30 日前）披露",
                ),
            ],
        ),
        settlement="中国结算 A 股 T+1 交收；资金 T+1 可用、T+1 可取",
        price_anchor="收盘价",
        backtest_anchor="收盘价",
        live_window="14:30-14:57 下单，以接近收盘价成交为目标",
        status=MarketRuleStatus.DRAFT,
    )
