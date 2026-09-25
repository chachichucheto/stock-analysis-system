from __future__ import annotations

from datetime import date

from assoc.config import Thresholds
from assoc.scoring.end_rules import EndRuleInput, check_end
from assoc.timeutil import add_business_days
from market_helpers import business_days

THRESHOLDS = Thresholds()


def _base(**overrides) -> EndRuleInput:
    days = business_days(date(2026, 9, 28), 2)
    base = dict(
        started=True,
        start_date=days[0],
        created_date=days[0],
        eval_date=days[1],
        expected_days=10,
        tier1_reacted=False,
        candidate_reacted=False,
        priced_in_value=0.0,
        end_condition_triggered=False,
    )
    base.update(overrides)
    return EndRuleInput(**base)


def test_broken_condition_overrides_everything():
    inp = _base(end_condition_triggered=True, priced_in_value=0.9, tier1_reacted=True, candidate_reacted=True)
    assert check_end(inp, THRESHOLDS) == "シナリオが崩れた"


def test_waiting_scenario_not_started_within_limit_stays_active():
    created = business_days(date(2026, 9, 28), 1)[0]
    eval_date = add_business_days(created, THRESHOLDS.not_started_days - 1)
    inp = _base(started=False, start_date=None, created_date=created, eval_date=eval_date)
    assert check_end(inp, THRESHOLDS) is None


def test_waiting_scenario_ends_when_not_started_limit_reached():
    created = business_days(date(2026, 9, 28), 1)[0]
    eval_date = add_business_days(created, THRESHOLDS.not_started_days)
    inp = _base(started=False, start_date=None, created_date=created, eval_date=eval_date)
    assert check_end(inp, THRESHOLDS) == "起動しなかった"


def test_priced_in_completion_ends_the_scenario():
    inp = _base(priced_in_value=THRESHOLDS.priced_in_done)
    assert check_end(inp, THRESHOLDS) == "織り込みが完了した"


def test_below_priced_in_completion_does_not_end():
    inp = _base(priced_in_value=THRESHOLDS.priced_in_done - 0.01)
    assert check_end(inp, THRESHOLDS) is None


def test_weak_material_when_expected_period_passed_with_no_reaction():
    start = business_days(date(2026, 9, 28), 1)[0]
    eval_date = add_business_days(start, 11)  # expected_days=10 を超えた
    inp = _base(start_date=start, eval_date=eval_date, expected_days=10)
    assert check_end(inp, THRESHOLDS) == "材料が弱かった"


def test_association_did_not_reach_the_market_when_tier1_reacted_but_candidate_did_not():
    start = business_days(date(2026, 9, 28), 1)[0]
    eval_date = add_business_days(start, 11)
    inp = _base(start_date=start, eval_date=eval_date, expected_days=10, tier1_reacted=True, candidate_reacted=False)
    assert check_end(inp, THRESHOLDS) == "連想が市場に届かなかった"


def test_exactly_at_expected_days_boundary_is_still_active():
    start = business_days(date(2026, 9, 28), 1)[0]
    eval_date = add_business_days(start, 10)  # ちょうど想定期間(超えていない)
    inp = _base(start_date=start, eval_date=eval_date, expected_days=10)
    assert check_end(inp, THRESHOLDS) is None


def test_both_reacted_after_expected_period_is_not_an_end_condition_here():
    start = business_days(date(2026, 9, 28), 1)[0]
    eval_date = add_business_days(start, 11)
    inp = _base(
        start_date=start, eval_date=eval_date, expected_days=10, tier1_reacted=True, candidate_reacted=True
    )
    assert check_end(inp, THRESHOLDS) is None
