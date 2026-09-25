from __future__ import annotations

from datetime import date

import pytest

from assoc.scoring.priced_in import priced_in
from market_helpers import business_days, make_price_frame


def test_priced_in_is_zero_when_excess_is_zero():
    days = business_days(date(2026, 9, 28), 6)
    stock = make_price_frame(days, [100] * 6)
    topix = make_price_frame(days, [200] * 6)

    result = priced_in(stock, topix, days[1], days[-1], expected_rise_median=0.4, expected_rise_version=3)
    assert result.value == pytest.approx(0.0)
    assert result.expected_rise_version == 3


def test_priced_in_negative_excess_is_clamped_to_zero():
    days = business_days(date(2026, 9, 28), 6)
    stock = make_price_frame(days, [100, 100, 90, 90, 90, 90])
    topix = make_price_frame(days, [200] * 6)

    result = priced_in(stock, topix, days[1], days[-1], expected_rise_median=0.4, expected_rise_version=1)
    assert result.value == 0.0


def test_priced_in_can_exceed_one():
    days = business_days(date(2026, 9, 28), 6)
    # 起点(days[0])から見て株が +80%、TOPIX 横ばい。想定上昇幅の中央値 +40% に対して 2.0。
    stock = make_price_frame(days, [100, 100, 180, 180, 180, 180])
    topix = make_price_frame(days, [200] * 6)

    result = priced_in(stock, topix, days[1], days[-1], expected_rise_median=0.4, expected_rise_version=1)
    assert result.value == pytest.approx(2.0)


def test_priced_in_rejects_non_positive_expected_rise():
    days = business_days(date(2026, 9, 28), 6)
    stock = make_price_frame(days, [100] * 6)
    topix = make_price_frame(days, [200] * 6)
    with pytest.raises(ValueError):
        priced_in(stock, topix, days[1], days[-1], expected_rise_median=0.0, expected_rise_version=1)
