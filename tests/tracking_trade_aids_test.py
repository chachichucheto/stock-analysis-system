from __future__ import annotations

from datetime import date

import pytest

from assoc.config import Thresholds
from assoc.market.indicators import daily_vol, price_limit
from assoc.timeutil import add_business_days
from assoc.tracking.trade_aids import trade_aids
from market_helpers import business_days, make_price_frame

THRESHOLDS = Thresholds()


def _stock(days):
    closes = [1000 + i for i in range(len(days))]
    return make_price_frame(days, closes)


def test_earnings_warning_when_within_expected_period():
    days = business_days(date(2026, 9, 28), 30)
    eval_date = days[-1]
    earnings_date = add_business_days(eval_date, 5)
    stock = _stock(days)

    result = trade_aids(
        eval_date, expected_days=10, price=1000.0, prices=stock,
        earnings_date=earnings_date, ex_rights_date=None,
        credit_restriction=False, thresholds=THRESHOLDS,
    )
    assert result.days_to_earnings == 5
    assert result.earnings_warning is True


def test_earnings_on_eval_date_itself_counts_as_zero_days():
    days = business_days(date(2026, 9, 28), 30)
    eval_date = days[-1]
    stock = _stock(days)

    result = trade_aids(
        eval_date, expected_days=10, price=1000.0, prices=stock,
        earnings_date=eval_date, ex_rights_date=None,
        credit_restriction=False, thresholds=THRESHOLDS,
    )
    assert result.days_to_earnings == 0
    assert result.earnings_warning is True


def test_no_earnings_warning_when_beyond_expected_period():
    days = business_days(date(2026, 9, 28), 30)
    eval_date = days[-1]
    earnings_date = add_business_days(eval_date, 15)
    stock = _stock(days)

    result = trade_aids(
        eval_date, expected_days=10, price=1000.0, prices=stock,
        earnings_date=earnings_date, ex_rights_date=None,
        credit_restriction=False, thresholds=THRESHOLDS,
    )
    assert result.earnings_warning is False


def test_no_earnings_date_gives_no_warning():
    days = business_days(date(2026, 9, 28), 30)
    stock = _stock(days)
    result = trade_aids(
        days[-1], expected_days=10, price=1000.0, prices=stock,
        earnings_date=None, ex_rights_date=None,
        credit_restriction=False, thresholds=THRESHOLDS,
    )
    assert result.days_to_earnings is None
    assert result.earnings_warning is False


def test_ex_rights_within_period_flag():
    days = business_days(date(2026, 9, 28), 30)
    eval_date = days[-1]
    ex_rights_date = add_business_days(eval_date, 3)
    stock = _stock(days)

    result = trade_aids(
        eval_date, expected_days=10, price=1000.0, prices=stock,
        earnings_date=None, ex_rights_date=ex_rights_date,
        credit_restriction=True, thresholds=THRESHOLDS,
    )
    assert result.ex_rights_within_period is True
    assert result.credit_restriction is True


def test_daily_vol_and_price_limit_are_delegated_to_indicators():
    days = business_days(date(2026, 9, 28), 25)
    eval_date = days[-1]
    stock = _stock(days)

    result = trade_aids(
        eval_date, expected_days=10, price=1234.0, prices=stock,
        earnings_date=None, ex_rights_date=None,
        credit_restriction=False, thresholds=THRESHOLDS,
    )
    assert result.daily_vol == pytest.approx(daily_vol(stock, eval_date, THRESHOLDS.vol_window))
    assert result.price_limit_yen == price_limit(1234.0)
