from __future__ import annotations

from datetime import date

import pytest

from assoc.market.indicators import (
    avg_turnover,
    daily_vol,
    day_excess_sigma,
    excess_return,
    price_limit,
    return_over,
)
from market_helpers import business_days, make_frame_source, make_price_frame


def test_excess_return_is_zero_when_topix_rises_by_the_same_amount():
    days = business_days(date(2026, 9, 28), 12)
    stock = make_price_frame(days, [1000 * (1.01**i) for i in range(len(days))])
    topix = make_price_frame(days, [2000 * (1.01**i) for i in range(len(days))])
    src = make_frame_source({"9999": stock, "1306": topix})

    stock_df = src.daily("9999", days[0], days[-1])
    topix_df = src.daily("1306", days[0], days[-1])

    excess = excess_return(stock_df, topix_df, days[1], days[-1])
    assert excess == pytest.approx(0.0, abs=1e-9)


def test_excess_return_uses_previous_close_as_the_starting_point():
    days = business_days(date(2026, 9, 28), 5)
    # 起点(days[1] の前日終値、つまり days[0])から見て、株が +20%、TOPIX が横ばい。
    stock_closes = [100, 100, 120, 120, 120]
    topix_closes = [50, 50, 50, 50, 50]
    stock = make_price_frame(days, stock_closes)
    topix = make_price_frame(days, topix_closes)

    excess = excess_return(stock, topix, days[1], days[-1])
    assert excess == pytest.approx(0.20, abs=1e-9)


def test_excess_return_can_be_negative():
    days = business_days(date(2026, 9, 28), 5)
    stock = make_price_frame(days, [100, 100, 90, 90, 90])
    topix = make_price_frame(days, [50, 50, 50, 50, 50])
    excess = excess_return(stock, topix, days[1], days[-1])
    assert excess == pytest.approx(-0.10, abs=1e-9)


def test_daily_vol_matches_manual_std_calculation():
    days = business_days(date(2026, 9, 28), 8)
    closes = [100, 101, 100, 101, 100, 101, 100, 101]
    stock = make_price_frame(days, closes)

    vol = daily_vol(stock, days[-1], window=5)

    import pandas as pd

    tail = pd.Series(closes[-6:])
    expected = tail.pct_change().dropna().std(ddof=1)
    assert vol == pytest.approx(expected)


def test_day_excess_sigma_detects_a_large_move_against_normal_volatility():
    days = business_days(date(2026, 9, 28), 8)
    # 普段は小さく上下し、最終日だけ +10% 跳ねる。TOPIX は横ばい。
    stock_closes = [100, 101, 100, 101, 100, 101, 100, 110]
    topix_closes = [200] * 8
    stock = make_price_frame(days, stock_closes)
    topix = make_price_frame(days, topix_closes)

    sigma = day_excess_sigma(stock, topix, days[-1], window=5)
    assert sigma > 5.0  # 普段の値動き(1%前後)に対して圧倒的に大きい


def test_day_excess_sigma_is_small_for_an_ordinary_day():
    days = business_days(date(2026, 9, 28), 8)
    stock_closes = [100, 101, 100, 101, 100, 101, 100, 101]
    topix_closes = [200] * 8
    stock = make_price_frame(days, stock_closes)
    topix = make_price_frame(days, topix_closes)

    sigma = day_excess_sigma(stock, topix, days[-1], window=5)
    assert abs(sigma) < 2.0  # reacted_sigma の初期値(2.0)未満


def test_avg_turnover_is_the_mean_of_close_times_volume():
    days = business_days(date(2026, 9, 28), 5)
    closes = [100, 200, 300, 400, 500]
    volumes = [10, 10, 10, 10, 10]
    stock = make_price_frame(days, closes, volumes)

    turnover = avg_turnover(stock, days[-1], window=5)
    expected = sum(c * v for c, v in zip(closes, volumes)) / 5
    assert turnover == pytest.approx(expected)


def test_return_over_days():
    days = business_days(date(2026, 9, 28), 6)
    closes = [100, 100, 100, 100, 100, 150]
    stock = make_price_frame(days, closes)

    r = return_over(stock, days[-1], days=5)
    assert r == pytest.approx(0.50)


@pytest.mark.parametrize(
    "price,expected_width",
    [
        (0, 30),
        (99, 30),
        (100, 50),
        (199, 50),
        (200, 80),
        (499, 80),
        (500, 100),
        (699, 100),
        (700, 150),
        (999, 150),
        (1_000, 300),
        (1_499, 300),
        (10_000, 3_000),
        (50_000_000, 10_000_000),
        (100_000_000, 10_000_000),  # 表の最上限を超えても最大の幅を使う
    ],
)
def test_price_limit_table_boundaries(price, expected_width):
    assert price_limit(price) == expected_width


def test_price_limit_rejects_negative_price():
    with pytest.raises(ValueError):
        price_limit(-1)
