"""売買の目安。docs/CONCEPT.md §9.1、docs/DESIGN.md §8.2。

決算発表までの営業日数(想定期間内なら警告)、権利落ち日、信用規制、普段の値動き、
値幅制限をまとめる。決算日・権利落ち日・信用規制は、既存の master/カレンダーから
呼び出し側が取得して引数で渡す(このモジュールは計算だけを行う)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from assoc.config import Thresholds
from assoc.market.indicators import daily_vol, price_limit
from assoc.timeutil import business_days_between


@dataclass(frozen=True)
class TradeAidsResult:
    days_to_earnings: int | None
    earnings_warning: bool
    """決算発表が想定期間内(0〜expected_days 営業日後)にあるか。"""
    days_to_ex_rights: int | None
    ex_rights_within_period: bool
    credit_restriction: bool
    daily_vol: float
    price_limit_yen: float


def trade_aids(
    eval_date: date,
    expected_days: int,
    price: float,
    prices: pd.DataFrame,
    earnings_date: date | None,
    ex_rights_date: date | None,
    credit_restriction: bool,
    thresholds: Thresholds,
) -> TradeAidsResult:
    days_to_earnings = business_days_between(eval_date, earnings_date) if earnings_date else None
    earnings_warning = days_to_earnings is not None and 0 <= days_to_earnings <= expected_days

    days_to_ex_rights = business_days_between(eval_date, ex_rights_date) if ex_rights_date else None
    ex_rights_within = days_to_ex_rights is not None and 0 <= days_to_ex_rights <= expected_days

    vol = daily_vol(prices, eval_date, thresholds.vol_window)
    limit = price_limit(price)

    return TradeAidsResult(
        days_to_earnings=days_to_earnings,
        earnings_warning=earnings_warning,
        days_to_ex_rights=days_to_ex_rights,
        ex_rights_within_period=ex_rights_within,
        credit_restriction=credit_restriction,
        daily_vol=vol,
        price_limit_yen=limit,
    )
