from __future__ import annotations

from assoc.config import Thresholds
from assoc.tracking.sell_signals import SellSignalInput, sell_signals

THRESHOLDS = Thresholds()  # priced_in_near=0.70, ending_soon_days=2


def test_no_signals_when_nothing_applies():
    inp = SellSignalInput(
        break_condition_triggered=False,
        scenario_weakened_or_dead=False,
        priced_in_value=0.3,
        remaining_days=5,
    )
    assert sell_signals(inp, THRESHOLDS) == []


def test_break_condition_signal():
    inp = SellSignalInput(True, False, 0.0, 5)
    signals = sell_signals(inp, THRESHOLDS)
    assert "崩れる条件に当たるニュースが出た" in signals


def test_scenario_weakened_signal():
    inp = SellSignalInput(False, True, 0.0, 5)
    signals = sell_signals(inp, THRESHOLDS)
    assert "シナリオが弱体化・消滅した" in signals


def test_priced_in_near_completion_signal_at_the_boundary():
    inp = SellSignalInput(False, False, THRESHOLDS.priced_in_near, 5)
    signals = sell_signals(inp, THRESHOLDS)
    assert "織り込み度が70%以上(完了が近い)" in signals


def test_priced_in_just_below_boundary_has_no_signal():
    inp = SellSignalInput(False, False, THRESHOLDS.priced_in_near - 0.01, 5)
    assert sell_signals(inp, THRESHOLDS) == []


def test_ending_soon_signal_at_the_boundary():
    inp = SellSignalInput(False, False, 0.0, THRESHOLDS.ending_soon_days)
    signals = sell_signals(inp, THRESHOLDS)
    assert "想定期間の終わりが近い" in signals


def test_all_four_signals_together():
    inp = SellSignalInput(True, True, 0.9, 0)
    signals = sell_signals(inp, THRESHOLDS)
    assert len(signals) == 4
