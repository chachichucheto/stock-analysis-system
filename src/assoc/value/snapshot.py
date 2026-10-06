"""ある日(asof)に知り得る情報だけで Snapshot を組み立てる。先読み防止の入口(docs/VALUE_DESIGN.md §6.1)。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from assoc.value import metrics
from assoc.value.data import FinancialsStore
from assoc.value.models import Snapshot, UniverseRow
from assoc.value.thresholds import ValueThresholds


def build_snapshot(u: UniverseRow, asof: date, fin: FinancialsStore, prices: pd.DataFrame,
                   th: ValueThresholds) -> Snapshot | None:
    """prices は全期間を渡してよい。asof より後は、ここで切り捨てる。asof の終値が無い銘柄は None(売買できない日)。"""
    sliced = prices[prices["date"] <= asof].reset_index(drop=True)
    if sliced.empty or sliced.iloc[-1]["date"] != asof:
        return None
    rows = fin.known_rows(u.code, asof)
    latest = rows[0] if rows else None
    prev = rows[1] if len(rows) > 1 else None
    fy = [r for r in rows if r.period == "FY"]
    price = float(sliced.iloc[-1]["close"])
    snap = Snapshot(
        code=u.code, name=u.name, sector33=u.sector33, asof=asof, prices=sliced, fin=latest,
        fin_year_ago=FinancialsStore.year_ago(rows, latest) if latest else None,
        fin_prev=prev,
        fin_prev_year_ago=FinancialsStore.year_ago(rows, prev) if prev else None,
        fy_history=fy, price=price,
        mcap=metrics.market_cap(price, latest) if latest else None,
        turnover=metrics.avg_turnover(sliced, th.turnover_window),
    )
    return snap


def passes_base(s: Snapshot, th: ValueThresholds) -> tuple[bool, list[str]]:
    """段階1の足切り(時価総額・流動性・低位株・財務の有無)。通らない理由を返す。"""
    why = []
    if s.fin is None:
        why.append("開示済みの財務データなし")
    if s.mcap is None:
        why.append("時価総額を計算できない")
    elif not (th.mcap_min_yen <= s.mcap <= th.mcap_max_yen):
        why.append(f"時価総額 {s.mcap / 1e8:.0f}億円が範囲外({th.mcap_min_yen / 1e8:.0f}〜{th.mcap_max_yen / 1e8:.0f}億円)")
    if s.turnover is None or s.turnover < th.min_turnover_yen:
        t = "不明" if s.turnover is None else f"{s.turnover / 1e4:.0f}万円"
        why.append(f"売買代金(20日平均){t}が下限 {th.min_turnover_yen / 1e4:.0f}万円未満")
    if s.price < th.min_price_yen:
        why.append(f"株価 {s.price:.0f}円が下限 {th.min_price_yen:.0f}円未満")
    return (not why, why)
