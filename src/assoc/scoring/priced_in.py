"""織り込み度。docs/CONCEPT.md §8.1、docs/DESIGN.md §6.6。

織り込み度 = 起動日の前日終値からの TOPIX 超過上昇 ÷ 想定上昇幅の中央値。
負は 0 に丸める。上限は設けない(1.0 を超えることもある)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from assoc.market.indicators import excess_return


@dataclass(frozen=True)
class PricedInResult:
    value: float
    """織り込み度(0 以上。上限なし)。"""
    expected_rise_version: int
    """計算に使った想定上昇幅の版番号。"""
    excess: float
    """分子として使った TOPIX 超過上昇(参考値)。"""


def priced_in(
    prices: pd.DataFrame,
    topix: pd.DataFrame,
    start_date: date,
    eval_date: date,
    expected_rise_median: float,
    expected_rise_version: int,
) -> PricedInResult:
    """start_date は起動日(前日終値が起点になる)。"""
    if expected_rise_median <= 0:
        raise ValueError("expected_rise_median は正の値である必要があります")
    excess = excess_return(prices, topix, start_date, eval_date)
    value = max(0.0, excess / expected_rise_median)
    return PricedInResult(value=value, expected_rise_version=expected_rise_version, excess=excess)
