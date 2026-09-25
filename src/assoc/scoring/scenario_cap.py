"""進行中・待機中のシナリオの上限。docs/CONCEPT.md §12.2、docs/DESIGN.md §7。

Thresholds.max_active_scenarios を超えたら、自信度の低いものから休眠にする対象を返す。
"""
from __future__ import annotations

from dataclasses import dataclass

from assoc.config import Thresholds


@dataclass(frozen=True)
class ScenarioForCap:
    scenario_id: str
    confidence: float
    """そのシナリオを代表する自信度(例:シナリオ内の候補の自信度の最大値)。"""


def scenarios_to_dormant(scenarios: list[ScenarioForCap], thresholds: Thresholds) -> list[ScenarioForCap]:
    """上限を超えた分だけ、自信度の低い順に休眠対象として返す(超えていなければ空リスト)。"""
    excess = len(scenarios) - thresholds.max_active_scenarios
    if excess <= 0:
        return []
    ordered = sorted(scenarios, key=lambda s: s.confidence)
    return ordered[:excess]
