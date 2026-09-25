"""株価の指標(純粋な計算関数)。docs/DESIGN.md §6.6・§8.2、docs/CONCEPT.md §8.1。

すべて pandas.DataFrame(market.prices.COLUMNS の列を持つ、date 昇順)を受け取る。
株価の取得(PriceSource)は呼び出し側の責任とし、ここでは計算だけを行う。
"""
from __future__ import annotations

from datetime import date

import pandas as pd


def _close_before(prices: pd.DataFrame, d: date) -> float:
    """d より前(d を含まない)の最後の取引日の終値。「前日終値」に使う。"""
    sub = prices[prices["date"] < d]
    if sub.empty:
        raise ValueError(f"{d} より前の株価データがありません")
    return float(sub.iloc[-1]["close"])


def _close_on_or_before(prices: pd.DataFrame, d: date) -> float:
    """d 以前の最後の取引日の終値。d 自身が取引日ならその終値。"""
    sub = prices[prices["date"] <= d]
    if sub.empty:
        raise ValueError(f"{d} 以前の株価データがありません")
    return float(sub.iloc[-1]["close"])


def excess_return(prices: pd.DataFrame, topix: pd.DataFrame, start_date: date, end_date: date) -> float:
    """起点(start_date の前日終値)から end_date 終値までの TOPIX 超過上昇。

    docs/CONCEPT.md §8.1・§8.4「織り込み度」の分子、DESIGN §6.8「等級の確定」に使う。
    戻り値は小数(0.10 = +10%)。
    """
    stock_ref = _close_before(prices, start_date)
    stock_end = _close_on_or_before(prices, end_date)
    topix_ref = _close_before(topix, start_date)
    topix_end = _close_on_or_before(topix, end_date)
    stock_return = stock_end / stock_ref - 1.0
    topix_return = topix_end / topix_ref - 1.0
    return stock_return - topix_return


def daily_vol(prices: pd.DataFrame, end_date: date, window: int) -> float:
    """普段の値動き:end_date までの直近 window 営業日の日次リターンの標準偏差(標本標準偏差)。

    docs/DESIGN.md §7「1段目の反応した」の分母、§8.2「売買の目安」に使う。
    """
    sub = prices[prices["date"] <= end_date].tail(window + 1)
    if len(sub) < 3:
        raise ValueError("普段の値動きを計算するにはデータが足りません")
    returns = sub["close"].pct_change().dropna()
    return float(returns.std(ddof=1))


def day_excess_sigma(prices: pd.DataFrame, topix: pd.DataFrame, date_: date, window: int) -> float:
    """当日の TOPIX 超過 ÷ 普段の値動き。等級の検算・「反応した」の判定に使う(docs/DESIGN.md §5.3・§6.8)。

    「普段の値動き」は date_ の前日までの window 営業日で計算し、当日の値動きで汚染しない。
    """
    day_excess = excess_return(prices, topix, date_, date_)
    prior = prices[prices["date"] < date_]
    if prior.empty:
        raise ValueError(f"{date_} より前の株価データがありません")
    prior_end = prior.iloc[-1]["date"]
    vol = daily_vol(prices, prior_end, window)
    if vol == 0:
        return float("inf") if day_excess != 0 else 0.0
    return day_excess / vol


def avg_turnover(prices: pd.DataFrame, end_date: date, window: int) -> float:
    """売買代金(close × volume)の、end_date までの直近 window 営業日の平均。流動性の下限の判定に使う。"""
    sub = prices[prices["date"] <= end_date].tail(window)
    if sub.empty:
        raise ValueError("売買代金を計算するにはデータが足りません")
    turnover = sub["close"] * sub["volume"]
    return float(turnover.mean())


def return_over(prices: pd.DataFrame, end_date: date, days: int) -> float:
    """end_date までの直近 days 営業日の騰落率。「直近で急騰済み」の判定に使う。"""
    sub = prices[prices["date"] <= end_date]
    if len(sub) < days + 1:
        raise ValueError("騰落率を計算するにはデータが足りません")
    base = float(sub.iloc[-(days + 1)]["close"])
    end = float(sub.iloc[-1]["close"])
    return end / base - 1.0


# 値幅制限(呼値の基準となる価格帯ごとの制限値幅)。
# 出典:東証(日本取引所グループ)業務規程施行規則 別表4「値幅制限に関する規則」の公表値。
# (基準値段の下限, 制限値幅[円])。基準値段が下限以上・次の下限未満の行を採用する。
_PRICE_LIMIT_TABLE: tuple[tuple[float, float], ...] = (
    (0, 30),
    (100, 50),
    (200, 80),
    (500, 100),
    (700, 150),
    (1_000, 300),
    (1_500, 400),
    (2_000, 500),
    (3_000, 700),
    (5_000, 1_000),
    (7_000, 1_500),
    (10_000, 3_000),
    (15_000, 4_000),
    (20_000, 5_000),
    (30_000, 7_000),
    (50_000, 10_000),
    (70_000, 15_000),
    (100_000, 30_000),
    (150_000, 40_000),
    (200_000, 50_000),
    (300_000, 70_000),
    (500_000, 100_000),
    (700_000, 150_000),
    (1_000_000, 300_000),
    (1_500_000, 400_000),
    (2_000_000, 500_000),
    (3_000_000, 700_000),
    (5_000_000, 1_000_000),
    (7_000_000, 1_500_000),
    (10_000_000, 3_000_000),
    (15_000_000, 4_000_000),
    (20_000_000, 5_000_000),
    (30_000_000, 7_000_000),
    (50_000_000, 10_000_000),
)


def price_limit(price: float) -> float:
    """price(基準値段)に対する、その日の制限値幅(円)。ストップ高・ストップ安の帯の幅。"""
    if price < 0:
        raise ValueError("price は 0 以上である必要があります")
    width = _PRICE_LIMIT_TABLE[0][1]
    for lower, w in _PRICE_LIMIT_TABLE:
        if price >= lower:
            width = w
        else:
            break
    return float(width)
