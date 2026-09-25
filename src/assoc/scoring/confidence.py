"""自信度。docs/CONCEPT.md §8.4、docs/DESIGN.md §6.6。

自信度 = 実現確度 × 残り余地 × 鮮度の補正
残り余地 = 想定上昇幅(中央値) × max(0, 1 − 織り込み度)
鮮度の補正 = 鮮度そのもの(0〜1)
"""
from __future__ import annotations

from dataclasses import dataclass

from assoc.config import Thresholds


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float
    remaining_room: float
    stars: int
    """1〜4個。"""


def remaining_room(expected_rise_median: float, priced_in_value: float) -> float:
    return expected_rise_median * max(0.0, 1.0 - priced_in_value)


def star_rating(confidence_value: float, star_thresholds: tuple[float, float, float]) -> int:
    """Thresholds.star_thresholds は ★2〜★4 の自信度の下限。それ未満は ★1。"""
    t2, t3, t4 = star_thresholds
    if confidence_value >= t4:
        return 4
    if confidence_value >= t3:
        return 3
    if confidence_value >= t2:
        return 2
    return 1


def confidence(
    realization_prob: float,
    expected_rise_median: float,
    priced_in_value: float,
    freshness_value: float,
    thresholds: Thresholds,
) -> ConfidenceResult:
    room = remaining_room(expected_rise_median, priced_in_value)
    value = realization_prob * room * freshness_value
    stars = star_rating(value, thresholds.star_thresholds)
    return ConfidenceResult(confidence=value, remaining_room=room, stars=stars)
