from __future__ import annotations

from datetime import date

from assoc.config import Thresholds
from assoc.tracking.grade_check import check_grade
from market_helpers import business_days, make_price_frame

THRESHOLDS = Thresholds()  # reacted_sigma = 2.0


def _frames(spike: bool):
    days = business_days(date(2026, 9, 28), 8)
    if spike:
        stock_closes = [100, 101, 100, 101, 100, 101, 100, 115]
    else:
        stock_closes = [100, 101, 100, 101, 100, 101, 100, 101]
    topix_closes = [200] * 8
    stock = make_price_frame(days, stock_closes)
    topix = make_price_frame(days, topix_closes)
    return days, stock, topix


def test_check_basis_none_leaves_the_provisional_grade_as_is():
    days, stock, topix = _frames(spike=False)
    result = check_grade("S", "なし", [], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS)
    assert result.final_grade == "S"
    assert result.reacted is None
    assert result.note == "検算なし"


def test_empty_check_codes_also_means_no_check():
    days, stock, topix = _frames(spike=True)
    result = check_grade("A", "1段目", [], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS)
    assert result.final_grade == "A"
    assert result.reacted is None
    assert result.note == "検算なし"


def test_provisional_s_is_downgraded_to_a_when_no_reaction():
    days, stock, topix = _frames(spike=False)
    result = check_grade(
        "S", "1段目", ["1000"], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS
    )
    assert result.reacted is False
    assert result.final_grade == "A"
    assert result.note == "反応なしのため1段階格下げ"


def test_provisional_a_is_downgraded_to_b_when_no_reaction():
    days, stock, topix = _frames(spike=False)
    result = check_grade(
        "A", "1段目", ["1000"], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS
    )
    assert result.final_grade == "B"


def test_provisional_s_is_confirmed_when_reacted():
    days, stock, topix = _frames(spike=True)
    result = check_grade(
        "S", "1段目", ["1000"], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS
    )
    assert result.reacted is True
    assert result.final_grade == "S"
    assert result.note == "確定"


def test_provisional_b_flags_possible_oversight_when_reacted():
    days, stock, topix = _frames(spike=True)
    result = check_grade(
        "B", "1段目", ["1000"], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS
    )
    assert result.final_grade == "B"  # 格付けは変えない
    assert result.note == "見落としの可能性"


def test_provisional_c_flags_possible_oversight_when_reacted():
    days, stock, topix = _frames(spike=True)
    result = check_grade(
        "C", "業種指数", ["1000"], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS
    )
    assert result.final_grade == "C"
    assert result.note == "見落としの可能性"


def test_provisional_b_confirmed_without_flag_when_no_reaction():
    days, stock, topix = _frames(spike=False)
    result = check_grade(
        "B", "1段目", ["1000"], days[-1], {"1000": stock}, topix, window=5, thresholds=THRESHOLDS
    )
    assert result.final_grade == "B"
    assert result.note == "確定"


def test_max_sigma_across_multiple_check_codes():
    days, spiking_stock, topix = _frames(spike=True)
    _, flat_stock, _ = _frames(spike=False)
    result = check_grade(
        "B",
        "銘柄群",
        ["flat", "spiking"],
        days[-1],
        {"flat": flat_stock, "spiking": spiking_stock},
        topix,
        window=5,
        thresholds=THRESHOLDS,
    )
    assert result.reacted is True
    assert result.note == "見落としの可能性"
