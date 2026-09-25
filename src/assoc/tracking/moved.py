"""「動いた」の判定。docs/CONCEPT.md §1.2・§7、docs/DESIGN.md §6.8。

本命に挙げた日(first_pick_date)の前日終値を起点に、最長
min(想定期間, moved_max_days) 営業日のうちに TOPIX 超過が moved_excess 以上に
なったかを判定する。動き出した日(初めて move_start_excess を超えた日)と、
動くまでの営業日数も返す。

強化されても期間は延ばさない(CONCEPT §8.1)ため、expected_days には
本命に挙げた時点の想定期間をそのまま渡すこと(呼び出し側の責任)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from assoc.config import Thresholds
from assoc.market.indicators import excess_return
from assoc.timeutil import add_business_days, business_days_between


@dataclass(frozen=True)
class MovedResult:
    moved: bool
    moved_at: date | None
    """動き出した日(初めて move_start_excess を超えた日)。"""
    days_to_move: int | None
    """first_pick_date から動き出した日までの営業日数。"""
    max_excess: float
    """期間内で観測された TOPIX 超過の最大値。"""


def check_moved(
    prices: pd.DataFrame,
    topix: pd.DataFrame,
    first_pick_date: date,
    expected_days: int,
    thresholds: Thresholds,
) -> MovedResult:
    horizon_days = min(expected_days, thresholds.moved_max_days)
    end_date = add_business_days(first_pick_date, horizon_days)

    candidate_dates = sorted(
        d for d in prices["date"].tolist() if first_pick_date <= d <= end_date
    )

    max_excess = 0.0
    moved = False
    moved_at: date | None = None
    for d in candidate_dates:
        excess = excess_return(prices, topix, first_pick_date, d)
        if excess > max_excess:
            max_excess = excess
        if moved_at is None and excess >= thresholds.move_start_excess:
            moved_at = d
        if excess >= thresholds.moved_excess:
            moved = True

    days_to_move = business_days_between(first_pick_date, moved_at) if moved_at is not None else None
    return MovedResult(moved=moved, moved_at=moved_at, days_to_move=days_to_move, max_excess=max_excess)
