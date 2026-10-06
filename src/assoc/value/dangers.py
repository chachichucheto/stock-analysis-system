"""危険信号(docs/VALUE_DESIGN.md §6.3)。block は候補から除外、warn は減点。"""
from __future__ import annotations

from assoc.value import metrics
from assoc.value.models import Danger, Snapshot, UniverseRow
from assoc.value.thresholds import ValueThresholds


def detect_dangers(s: Snapshot, th: ValueThresholds, master: UniverseRow | None = None) -> list[Danger]:
    """master を渡すと、監理・整理/継続企業の疑義のフラグも見る(現在の状態なので、過去検証では渡さない)。"""
    out: list[Danger] = []
    if master is not None:
        if master.monitoring:
            out.append(Danger("block", "監理・整理ポスト"))
        if master.going_concern:
            out.append(Danger("block", "継続企業の疑義"))
    f = s.fin
    if f is not None:
        if f.equity is not None and f.equity <= 0:
            out.append(Danger("block", "債務超過(純資産が0以下)"))
        d = metrics.dilution(f, s.fin_year_ago)
        if d is not None and d > th.dilution_block:
            out.append(Danger("block", f"発行済株式数が前年から {d:.0%} 増加(大きな希薄化)"))
        elif d is not None and d > th.dilution_warn:
            out.append(Danger("warn", f"発行済株式数が前年から {d:.0%} 増加(希薄化)"))
        cashlike = metrics.liquid_cash_like(f)
        loss = f.operating_income_ttm is not None and f.operating_income_ttm < 0
        if loss and f.interest_debt is not None and cashlike is not None and f.interest_debt > 3 * max(cashlike, 1.0):
            out.append(Danger("warn", "赤字で、有利子負債が現預金の3倍超"))
    r20 = metrics.ret_over(s.prices, 20)
    if r20 is not None and r20 > th.surged_ret20:
        out.append(Danger("warn", f"直近20営業日で {r20:.0%} 上昇(急騰済み)"))
    return out
