from __future__ import annotations

import pytest

from assoc.config import Thresholds
from assoc.scoring.confidence import confidence, remaining_room, star_rating

THRESHOLDS = Thresholds()  # star_thresholds = (0.05, 0.10, 0.20)


def test_remaining_room_formula():
    assert remaining_room(expected_rise_median=0.4, priced_in_value=0.25) == pytest.approx(0.3)


def test_remaining_room_is_never_negative_even_if_priced_in_exceeds_one():
    assert remaining_room(expected_rise_median=0.4, priced_in_value=1.5) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "value,expected_stars",
    [
        (0.0, 1),
        (0.049, 1),
        (0.05, 2),  # ★2の下限ちょうど
        (0.099, 2),
        (0.10, 3),  # ★3の下限ちょうど
        (0.199, 3),
        (0.20, 4),  # ★4の下限ちょうど
        (1.0, 4),
    ],
)
def test_star_rating_boundaries(value, expected_stars):
    assert star_rating(value, THRESHOLDS.star_thresholds) == expected_stars


def test_confidence_multiplies_the_three_factors():
    result = confidence(
        realization_prob=0.5,
        expected_rise_median=0.4,
        priced_in_value=0.25,
        freshness_value=0.8,
        thresholds=THRESHOLDS,
    )
    # remaining_room = 0.4 * (1 - 0.25) = 0.3 ; confidence = 0.5 * 0.3 * 0.8 = 0.12
    assert result.remaining_room == pytest.approx(0.3)
    assert result.confidence == pytest.approx(0.12)
    assert result.stars == 3  # 0.10 <= 0.12 < 0.20


def test_confidence_is_zero_when_freshness_is_zero():
    result = confidence(
        realization_prob=0.9,
        expected_rise_median=0.6,
        priced_in_value=0.0,
        freshness_value=0.0,
        thresholds=THRESHOLDS,
    )
    assert result.confidence == 0.0
    assert result.stars == 1
