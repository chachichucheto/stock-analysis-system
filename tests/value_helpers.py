"""割安カタリスト・モデルのテスト用ヘルパー(合成データ作り)。pytest には集められない。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from assoc.market.prices import COLUMNS
from assoc.timeutil import add_business_days
from assoc.value.data import FinancialsStore
from assoc.value.models import FinancialRow, UniverseRow

ASOF = date(2026, 10, 6)   # 火曜日(営業日)


def days_ending(asof: date, n: int) -> list[date]:
    days = [asof]
    while len(days) < n:
        days.append(add_business_days(days[-1], -1))
    return sorted(days)


def frame(dates: list[date], closes: list[float], volumes: list[float] | float = 200_000.0) -> pd.DataFrame:
    vols = volumes if isinstance(volumes, list) else [volumes] * len(dates)
    return pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": vols})[COLUMNS]


def fin_row(code: str = "1000", period_end: date = date(2026, 3, 31), disclosed: date = date(2026, 5, 15),
            period: str = "FY", **kw) -> FinancialRow:
    """既定は「型Aに該当する」財務(株価350円・1,000万株で時価総額35億円、ネットネット指数0.58、PBR 0.39)。"""
    base = dict(cash=5e9, receivables=2e9, securities=0.0, investment_securities=1e9, allowance=0.0,
                current_assets=8.5e9, total_assets=11e9, total_liabilities=2e9, equity=9e9, interest_debt=0.5e9,
                revenue_ttm=10e9, operating_income_ttm=0.5e9, net_income_ttm=0.4e9, shares_ex_treasury=10_000_000.0)
    base.update(kw)
    return FinancialRow(code=code, period_end=period_end, disclosed_date=disclosed, period=period, **base)


def plain_fin(code: str, **kw) -> FinancialRow:
    """型Aに該当しない財務(負債が大きくネットネットの分母が0以下、PBR 1.17)。"""
    base = dict(cash=1e9, receivables=1e9, investment_securities=0.0, current_assets=3e9, total_assets=8e9,
                total_liabilities=5e9, equity=3e9)
    base.update(kw)
    return fin_row(code=code, **base)


def universe_row(code: str = "1000", sector: str = "機械", listed: date = date(2000, 1, 1),
                 delisted: date | None = None, **kw) -> UniverseRow:
    return UniverseRow(code=code, name=f"テスト{code}", market="スタンダード", sector33=sector,
                       listed_date=listed, delisted_date=delisted, **kw)


def store(*rows: FinancialRow) -> FinancialsStore:
    return FinancialsStore(list(rows))
