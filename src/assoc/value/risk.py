"""損失の限定(docs/VALUE_DESIGN.md §6.5)。銘柄選択とは別に、先に決めておく数式。

売買の指示ではなく、判断材料(株数の上限・損切りの目安)を出すだけ。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from assoc.value import metrics
from assoc.value.thresholds import ValueThresholds


@dataclass
class SizeResult:
    shares: int
    amount_yen: float
    risk_yen: float
    stop_price: float | None
    stop_distance: float | None
    limited_by: str                       # risk | liquidity | position_cap | skip
    notes: list[str] = field(default_factory=list)


def stop_price(prices: pd.DataFrame, entry: float, th: ValueThresholds) -> float | None:
    """損切りの目安=直近安値の少し下。株価データが足りなければ None。"""
    low = metrics.recent_low(prices, th.stop_window)
    if low is None:
        return None
    return min(low * (1 - th.stop_buffer), entry)


def position_size(capital: float, entry: float, stop: float | None, turnover_20d: float | None,
                  th: ValueThresholds) -> SizeResult:
    """1銘柄の株数=min(損失の上限 ÷ 1株の損失、売買代金の上限、総資金比の上限) を、単元株で切り捨て。"""
    if capital <= 0 or entry <= 0:
        raise ValueError("capital と entry は正の数にしてください")
    if stop is None or stop >= entry:
        return SizeResult(0, 0.0, 0.0, stop, None, "skip", ["損切りの目安を置けない(株価データ不足、または直近安値が現在値以上)"])
    distance = (entry - stop) / entry
    if distance > th.max_stop_distance:
        return SizeResult(0, 0.0, 0.0, stop, distance, "skip",
                          [f"損切り幅 {distance:.0%} が上限 {th.max_stop_distance:.0%} を超える。見送り"])
    by_risk = capital * th.risk_per_trade / (entry - stop)
    by_cap = capital * th.max_position_pct / entry
    candidates = {"risk": by_risk, "position_cap": by_cap}
    notes: list[str] = []
    if turnover_20d is not None and turnover_20d > 0:
        candidates["liquidity"] = turnover_20d * th.participation_max / entry
    else:
        notes.append("売買代金が不明のため、流動性の上限を適用していない")
    limited_by = min(candidates, key=candidates.get)
    shares = int(candidates[limited_by] // th.lot_size) * th.lot_size
    if shares <= 0:
        return SizeResult(0, 0.0, 0.0, stop, distance, "skip", notes + [f"{limited_by} の制約で1単元も買えない"])
    return SizeResult(shares, shares * entry, shares * (entry - stop), stop, distance, limited_by, notes)


def portfolio_check(capital: float, risks_yen: list[float], th: ValueThresholds) -> list[str]:
    """同時保有の損失合計が上限を超えていないか。超えていれば、その旨のメッセージを返す。"""
    total = sum(risks_yen)
    limit = capital * th.max_total_risk
    return [f"同時保有の損失合計 {total:,.0f}円 が上限 {limit:,.0f}円(総資金の{th.max_total_risk:.0%})を超える"] if total > limit else []
