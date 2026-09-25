from __future__ import annotations

from assoc.config import Thresholds
from assoc.scoring.scenario_cap import ScenarioForCap, scenarios_to_dormant

THRESHOLDS = Thresholds()  # max_active_scenarios = 15


def test_no_dormant_target_when_within_the_limit():
    scenarios = [ScenarioForCap(scenario_id=str(i), confidence=0.1) for i in range(THRESHOLDS.max_active_scenarios)]
    assert scenarios_to_dormant(scenarios, THRESHOLDS) == []


def test_dormant_targets_are_the_lowest_confidence_ones_over_the_limit():
    n = THRESHOLDS.max_active_scenarios + 3
    scenarios = [ScenarioForCap(scenario_id=str(i), confidence=float(i)) for i in range(n)]
    dormant = scenarios_to_dormant(scenarios, THRESHOLDS)
    assert len(dormant) == 3
    assert {s.scenario_id for s in dormant} == {"0", "1", "2"}


def test_exactly_at_the_limit_is_not_over():
    scenarios = [
        ScenarioForCap(scenario_id=str(i), confidence=0.1) for i in range(THRESHOLDS.max_active_scenarios + 1)
    ]
    dormant = scenarios_to_dormant(scenarios, THRESHOLDS)
    assert len(dormant) == 1
