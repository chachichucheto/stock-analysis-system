from __future__ import annotations

from datetime import date

import pytest

from assoc.config import Thresholds
from assoc.timeutil import business_days_between
from assoc.tracking.moved import check_moved
from market_helpers import business_days, make_price_frame

THRESHOLDS = Thresholds()  # moved_excess=0.10, move_start_excess=0.05, moved_max_days=20


def test_moved_true_with_correct_start_date_and_days_to_move():
    days = business_days(date(2026, 9, 28), 6)
    first_pick_date = days[1]
    closes = [100, 100, 103, 106, 111, 111]  # index0 は前日終値(起点)
    stock = make_price_frame(days, closes)
    topix = make_price_frame(days, [200] * 6)

    result = check_moved(stock, topix, first_pick_date, expected_days=10, thresholds=THRESHOLDS)

    assert result.moved is True
    assert result.moved_at == days[3]  # 初めて +5% を超えた日(+6%)
    assert result.days_to_move == business_days_between(first_pick_date, days[3])
    assert result.max_excess == pytest.approx(0.11)


def test_moved_false_when_excess_never_reaches_the_start_threshold():
    days = business_days(date(2026, 9, 28), 6)
    first_pick_date = days[1]
    closes = [100, 100, 101, 102, 102, 102]
    stock = make_price_frame(days, closes)
    topix = make_price_frame(days, [200] * 6)

    result = check_moved(stock, topix, first_pick_date, expected_days=10, thresholds=THRESHOLDS)

    assert result.moved is False
    assert result.moved_at is None
    assert result.days_to_move is None
    assert result.max_excess == pytest.approx(0.02)


def test_horizon_is_capped_by_moved_max_days_even_if_expected_days_is_larger():
    # expected_days(25) > moved_max_days(20) のため、期間は20営業日に切り詰められる。
    days = business_days(date(2026, 9, 28), 25)
    first_pick_date = days[1]
    closes = [100.0] * 25
    closes[23] = 300.0  # 期間の外(20営業日超)で急騰しても検出されない
    stock = make_price_frame(days, closes)
    topix = make_price_frame(days, [200.0] * 25)

    result = check_moved(stock, topix, first_pick_date, expected_days=25, thresholds=THRESHOLDS)

    assert result.moved is False
    assert result.moved_at is None


def test_strengthening_does_not_extend_the_window():
    # 「強化されても期間は延ばさない」(CONCEPT §8.1):呼び出し側は本命に挙げた時点の
    # expected_days を渡す契約になっており、moved_max_days による上限もそれを裏付ける。
    # ここでは、強化後の想定期間(仮に100営業日)を渡しても、20営業日を超えた急騰は
    # 捕捉されないことを確認する。
    days = business_days(date(2026, 9, 28), 25)
    first_pick_date = days[1]
    closes = [100.0] * 25
    closes[23] = 300.0
    stock = make_price_frame(days, closes)
    topix = make_price_frame(days, [200.0] * 25)

    result = check_moved(stock, topix, first_pick_date, expected_days=100, thresholds=THRESHOLDS)

    assert result.moved is False



def test_rise_on_pick_day_itself_is_not_counted():
    # レポートは引け後に出すので、本命に挙げた日の上昇は既に知っている。起点はその日の終値
    days = business_days(date(2026, 9, 28), 5)
    first_pick_date = days[1]
    closes = [100, 115, 116, 117, 117]  # 挙げた日に +15%、その後はほぼ横ばい
    stock = make_price_frame(days, closes)
    topix = make_price_frame(days, [200] * 5)

    result = check_moved(stock, topix, first_pick_date, expected_days=10, thresholds=THRESHOLDS)

    assert result.moved is False
    assert result.max_excess == pytest.approx(117 / 115 - 1)
