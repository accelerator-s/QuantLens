from __future__ import annotations

from datetime import date

from market_rules.sessions import SimpleTradingCalendar

# 数据来源：

CN_A_HOLIDAYS_2024: set[date] = {
    # 元旦
    date(2024, 1, 1),
    # 春节
    date(2024, 2, 9),
    date(2024, 2, 10),
    date(2024, 2, 11),
    date(2024, 2, 12),
    date(2024, 2, 13),
    date(2024, 2, 14),
    date(2024, 2, 15),
    date(2024, 2, 16),
    date(2024, 2, 17),
    # 清明节
    date(2024, 4, 4),
    date(2024, 4, 5),
    date(2024, 4, 6),
    # 劳动节
    date(2024, 5, 1),
    date(2024, 5, 2),
    date(2024, 5, 3),
    date(2024, 5, 4),
    date(2024, 5, 5),
    # 端午节
    date(2024, 6, 8),
    date(2024, 6, 9),
    date(2024, 6, 10),
    # 中秋节
    date(2024, 9, 15),
    date(2024, 9, 16),
    date(2024, 9, 17),
    # 国庆节
    date(2024, 10, 1),
    date(2024, 10, 2),
    date(2024, 10, 3),
    date(2024, 10, 4),
    date(2024, 10, 5),
    date(2024, 10, 6),
    date(2024, 10, 7),
}

CN_A_HOLIDAYS_2025: set[date] = {
    # 元旦
    date(2025, 1, 1),
    # 春节
    date(2025, 1, 28),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 1, 31),
    date(2025, 2, 1),
    date(2025, 2, 2),
    date(2025, 2, 3),
    date(2025, 2, 4),
    # 清明节
    date(2025, 4, 4),
    date(2025, 4, 5),
    date(2025, 4, 6),
    # 劳动节
    date(2025, 5, 1),
    date(2025, 5, 2),
    date(2025, 5, 3),
    date(2025, 5, 4),
    date(2025, 5, 5),
    # 端午节
    date(2025, 5, 31),
    date(2025, 6, 1),
    date(2025, 6, 2),
    # 中秋节 / 国庆节
    date(2025, 10, 1),
    date(2025, 10, 2),
    date(2025, 10, 3),
    date(2025, 10, 4),
    date(2025, 10, 5),
    date(2025, 10, 6),
    date(2025, 10, 7),
    date(2025, 10, 8),
}

CN_A_HOLIDAYS_2026: set[date] = {
    # 元旦
    date(2026, 1, 1),
    date(2026, 1, 2),
    date(2026, 1, 3),
    # 春节
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 2, 21),
    date(2026, 2, 22),
    date(2026, 2, 23),
    # 清明节
    date(2026, 4, 4),
    date(2026, 4, 5),
    date(2026, 4, 6),
    # 劳动节
    date(2026, 5, 1),
    date(2026, 5, 2),
    date(2026, 5, 3),
    # 端午节
    date(2026, 6, 19),
    date(2026, 6, 20),
    date(2026, 6, 21),
    # 中秋节
    date(2026, 9, 25),
    date(2026, 9, 26),
    date(2026, 9, 27),
    # 国庆节
    date(2026, 10, 1),
    date(2026, 10, 2),
    date(2026, 10, 3),
    date(2026, 10, 4),
    date(2026, 10, 5),
    date(2026, 10, 6),
    date(2026, 10, 7),
    date(2026, 10, 8),
}

CN_A_HOLIDAYS_2024_2026: set[date] = CN_A_HOLIDAYS_2024 | CN_A_HOLIDAYS_2025 | CN_A_HOLIDAYS_2026

class ChinaAShareCalendar(SimpleTradingCalendar):

    def __init__(self, extra_holidays: set[date] | None = None) -> None:
        holidays = CN_A_HOLIDAYS_2024_2026.copy()
        if extra_holidays:
            holidays |= extra_holidays
        super().__init__(holidays=holidays)
