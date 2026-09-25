"""鮮度。docs/CONCEPT.md §8.1、docs/DESIGN.md §6.6。

起動日(または直近の強化日)を100%とし、想定期間に対する経過営業日数の割合で0%まで
線形に減らす。待機中(started=False)は減らさない(100%のまま)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from assoc.timeutil import business_days_between


@dataclass(frozen=True)
class FreshnessInput:
    started: bool
    """False なら待機中(シナリオの時計はまだ動いていない)。"""
    start_date: date
    """起動日、または直近の強化日(強化されるたびにこの日に置き換える)。"""
    expected_days: int
    """想定期間(営業日)。"""
    eval_date: date
    """評価日。"""


def freshness(inp: FreshnessInput) -> float:
    """鮮度(0.0〜1.0)。"""
    if not inp.started:
        return 1.0
    if inp.expected_days <= 0:
        raise ValueError("expected_days は 1 以上である必要があります")
    elapsed = business_days_between(inp.start_date, inp.eval_date)
    if elapsed <= 0:
        return 1.0
    ratio = elapsed / inp.expected_days
    return max(0.0, 1.0 - ratio)
