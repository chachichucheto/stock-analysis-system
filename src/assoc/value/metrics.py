"""指標の計算(純粋な関数)。docs/VALUE_DESIGN.md §6.1。データが足りないときは None を返し、推測で埋めない。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from assoc.value.models import FinancialRow

# 資産の評価減(掛け目)。既定は「かぶ1000式」(掛け目なし)。保守版は検証で比べる(△ 提案)。
NO_HAIRCUT = {"receivables": 1.0, "securities": 1.0, "investment_securities": 1.0}
CONSERVATIVE = {"receivables": 0.8, "securities": 1.0, "investment_securities": 0.7}


def liquid_assets(f: FinancialRow, haircut: dict[str, float] | None = None) -> float | None:
    """かぶ1000式「換金性が高い流動資産」=現預金+売掛金+有価証券+投資有価証券−貸倒引当金。

    グレアム式と違い、投資有価証券を含める(紹介記事の定義 ○)。必須項目が欠けたら None。
    """
    h = haircut or NO_HAIRCUT
    if f.cash is None or f.receivables is None:
        return None
    total = f.cash + f.receivables * h["receivables"]
    total += (f.securities or 0.0) * h["securities"]
    total += (f.investment_securities or 0.0) * h["investment_securities"]
    total -= f.allowance or 0.0
    return total


def market_cap(price: float, f: FinancialRow) -> float | None:
    if f.shares_ex_treasury is None or f.shares_ex_treasury <= 0 or price <= 0:
        return None
    return price * f.shares_ex_treasury


def netnet_index(mcap: float | None, f: FinancialRow, haircut: dict[str, float] | None = None) -> float | None:
    """ネットネット指数 = 時価総額 ÷(換金性が高い流動資産 − 総負債)。分母が0以下なら None(該当しない)。"""
    la = liquid_assets(f, haircut)
    if mcap is None or la is None or f.total_liabilities is None:
        return None
    net = la - f.total_liabilities
    return mcap / net if net > 0 else None


def pbr(mcap: float | None, f: FinancialRow) -> float | None:
    if mcap is None or f.equity is None or f.equity <= 0:
        return None
    return mcap / f.equity


def equity_ratio(f: FinancialRow) -> float | None:
    if f.equity is None or f.total_assets is None or f.total_assets <= 0:
        return None
    return f.equity / f.total_assets


def per(mcap: float | None, f: FinancialRow) -> float | None:
    if mcap is None or f.net_income_ttm is None or f.net_income_ttm <= 0:
        return None
    return mcap / f.net_income_ttm


def yoy(latest: float | None, year_ago: float | None) -> float | None:
    """前年比。前年が0以下だと比が意味を持たないので None。"""
    if latest is None or year_ago is None or year_ago <= 0:
        return None
    return latest / year_ago - 1.0


def liquid_cash_like(f: FinancialRow) -> float | None:
    if f.cash is None:
        return None
    return f.cash + (f.securities or 0.0)


def dilution(f: FinancialRow, f_year_ago: FinancialRow | None) -> float | None:
    """発行済株式数(自己株式除く)の前年からの増加率。"""
    if f_year_ago is None or not f.shares_ex_treasury or not f_year_ago.shares_ex_treasury:
        return None
    return f.shares_ex_treasury / f_year_ago.shares_ex_treasury - 1.0


def fy_stats(fy_rows: list[FinancialRow]) -> dict[str, int] | None:
    """本決算の履歴の赤字年・黒字年の数と、直近からの連続赤字年数。営業利益が欠けた年があれば None。"""
    if any(r.operating_income_ttm is None for r in fy_rows):
        return None
    loss = sum(1 for r in fy_rows if r.operating_income_ttm < 0)
    profit = sum(1 for r in fy_rows if r.operating_income_ttm > 0)
    consecutive = 0
    for r in fy_rows:                      # 新しい順
        if r.operating_income_ttm < 0:
            consecutive += 1
        else:
            break
    return {"loss_years": loss, "profit_years": profit, "consecutive_loss_years": consecutive, "years": len(fy_rows)}


# ---- 株価の指標(prices は date 昇順で asof 以前のみ) -------------------------------

def ma_deviation(prices: pd.DataFrame, window: int) -> float | None:
    """終値の移動平均からの乖離率(-0.2 = 20%下方乖離)。"""
    if len(prices) < window:
        return None
    ma = float(prices["close"].tail(window).mean())
    return float(prices.iloc[-1]["close"]) / ma - 1.0 if ma > 0 else None


def volume_surge(prices: pd.DataFrame, mult: float, within: int, avg_window: int) -> bool | None:
    """直近 within 営業日のどこかで、出来高が直前 avg_window 日の平均の mult 倍以上になったか。"""
    if len(prices) < avg_window + within:
        return None
    vol = prices["volume"].reset_index(drop=True)
    n = len(vol)
    for i in range(n - within, n):
        base = vol.iloc[i - avg_window:i].mean()
        if base > 0 and vol.iloc[i] >= mult * base:
            return True
    return False


def drawdown_from_peak(prices: pd.DataFrame, window: int) -> float | None:
    """直近 window 営業日の最高終値からの下落率(-0.67 = 高値の1/3)。"""
    if len(prices) < 60:
        return None
    sub = prices.tail(window)
    peak = float(sub["close"].max())
    return float(prices.iloc[-1]["close"]) / peak - 1.0 if peak > 0 else None


def ret_over(prices: pd.DataFrame, days: int) -> float | None:
    if len(prices) < days + 1:
        return None
    base = float(prices.iloc[-(days + 1)]["close"])
    return float(prices.iloc[-1]["close"]) / base - 1.0 if base > 0 else None


def avg_turnover(prices: pd.DataFrame, window: int) -> float | None:
    if len(prices) < window:
        return None
    sub = prices.tail(window)
    return float((sub["close"] * sub["volume"]).mean())


def recent_low(prices: pd.DataFrame, window: int) -> float | None:
    if prices.empty:
        return None
    return float(prices.tail(window)["low"].min())
