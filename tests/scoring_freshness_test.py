from __future__ import annotations

from datetime import date

import pytest

from assoc.scoring.freshness import FreshnessInput, freshness
from assoc.timeutil import add_business_days
from market_helpers import business_days


def test_waiting_scenario_stays_at_full_freshness():
    days = business_days(date(2026, 9, 28), 30)
    inp = FreshnessInput(started=False, start_date=days[0], expected_days=10, eval_date=days[29])
    assert freshness(inp) == 1.0


def test_freshness_is_full_on_the_start_day_itself():
    days = business_days(date(2026, 9, 28), 5)
    inp = FreshnessInput(started=True, start_date=days[0], expected_days=10, eval_date=days[0])
    assert freshness(inp) == 1.0


def test_freshness_reaches_zero_exactly_at_the_expected_days_boundary():
    days = business_days(date(2026, 9, 28), 30)
    start = days[0]
    end = add_business_days(start, 10)  # ちょうど10営業日後
    inp = FreshnessInput(started=True, start_date=start, expected_days=10, eval_date=end)
    assert freshness(inp) == pytest.approx(0.0)


def test_freshness_does_not_go_negative_past_the_expected_period():
    days = business_days(date(2026, 9, 28), 40)
    start = days[0]
    end = add_business_days(start, 25)
    inp = FreshnessInput(started=True, start_date=start, expected_days=10, eval_date=end)
    assert freshness(inp) == 0.0


def test_freshness_is_half_at_the_midpoint():
    days = business_days(date(2026, 9, 28), 30)
    start = days[0]
    end = add_business_days(start, 5)
    inp = FreshnessInput(started=True, start_date=start, expected_days=10, eval_date=end)
    assert freshness(inp) == pytest.approx(0.5)


def test_freshness_crossing_new_year_holidays_uses_business_days_only():
    # 起動日を年末の直前にし、年末年始(12/31-1/3)と土日をまたいで評価する。
    start = date(2026, 12, 28)  # 月曜日を想定(business_days で補正される)
    start = business_days(start, 1)[0]
    end = add_business_days(start, 10)
    inp = FreshnessInput(started=True, start_date=start, expected_days=10, eval_date=end)
    # 年末年始をまたいでも、営業日でちょうど10日後なら鮮度は0%になる
    assert freshness(inp) == pytest.approx(0.0)
    # 年末年始をまたぐ間は暦日では10日をとうに超えているはずである
    assert (end - start).days > 10
