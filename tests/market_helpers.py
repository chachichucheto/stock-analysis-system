"""テスト用の共通ヘルパー(合成の株価データ作り)。テストファイルではないので pytest には集められない。

tests/scoring_*, tests/tracking_*, tests/market_* のテストから
`from market_helpers import ...` で使う(pytest がテストディレクトリを sys.path に
追加するため、同じディレクトリ内なら import できる)。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from assoc.market.prices import COLUMNS, FramePriceSource
from assoc.timeutil import add_business_days, is_business_day


def first_business_day_on_or_after(d: date) -> date:
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def business_days(start: date, n: int) -> list[date]:
    """start(を含む、直近の営業日に補正)から n 個の営業日のリスト。"""
    start = first_business_day_on_or_after(start)
    days = [start]
    while len(days) < n:
        days.append(add_business_days(days[-1], 1))
    return days


def make_price_frame(dates: list[date], closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """簡易な OHLCV。open/high/low は close と同じ値にする(テストでは使わない)。"""
    n = len(dates)
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": volumes,
        }
    )[COLUMNS]


def make_frame_source(frames: dict[str, pd.DataFrame]) -> FramePriceSource:
    return FramePriceSource(frames)


def constant_growth_closes(n: int, daily_rate: float, base: float = 1000.0) -> list[float]:
    closes = [base]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + daily_rate))
    return closes
