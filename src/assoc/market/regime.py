"""相場の姿勢と、底打ちの目印(D52)。

悪いニュースは世の中にいつもたくさんある。毎日「危ない」と言えば、何も買えなくなる。
そこで姿勢は、ニュースの数ではなく**相場そのものの傷み方**で決める(株価の事実だけ。LLM は理由を調べて、これを上げ下げする)。

  通常         :平時。悪材料は個別に見て、本命・保有に関係するものだけ対応する
  慎重         :下げが目立つ。新規は絞り、個別の悪材料に敏感になる
  投資を控える :大きく崩れている(1日-3%・5日-5%・高値から-12%のどれか)。新規の買いは止め、手持ちの見直しを優先する

底打ちの目印は「株価が落ち着いたか」だけを見る。**落ち着いたことは、投資を再開してよい理由にならない**
(下げた理由が解消されたかは、LLM が理由ごとに調べる。market_stance.causes)。
閾値は `config.Thresholds` の regime_*(暫定)。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from assoc.config import Thresholds

通常 = "通常"
慎重 = "慎重"
控える = "投資を控える"


def _closes(df: pd.DataFrame, day: date) -> list[tuple[date, float]]:
    if df is None or df.empty:
        return []
    rows = [(d, float(c)) for d, c in zip(df["date"], df["close"]) if d <= day and c == c]
    rows.sort(key=lambda x: x[0])
    return rows


def market_regime(topix: pd.DataFrame, tr: dict[str, Any], day: date, th: Thresholds) -> dict[str, Any]:
    rows = _closes(topix, day)
    if len(rows) < 6:
        return {"as_of": day.isoformat(), "level": 通常, "reasons": ["TOPIX の履歴が足りず、判定できません"],
                "metrics": {}, "stabilization": {"stabilized": False, "notes": ["履歴不足"]}}
    closes = [c for _, c in rows]
    last = closes[-1]
    ret = lambda n: last / closes[-1 - n] - 1 if len(closes) > n else None
    window = closes[-th.regime_high_days:]
    peak = max(window)
    dd = last / peak - 1
    breadth = [t for t in (tr.get("themes") or {}).values()
               if t.get("n_with_data") and t.get("median_5d") is not None and t["median_5d"] <= th.regime_theme_down]
    metrics = {"ret_1d": ret(1), "ret_5d": ret(5), "ret_20d": ret(20), "from_high": dd,
               "themes_down": len(breadth), "themes_total": len(tr.get("themes") or {})}

    reasons: list[str] = []
    level = 通常
    if metrics["ret_1d"] <= th.regime_out_1d:
        level = 控える; reasons.append(f"TOPIX が1日で {metrics['ret_1d']:+.1%}")
    if metrics["ret_5d"] is not None and metrics["ret_5d"] <= th.regime_out_5d:
        level = 控える; reasons.append(f"TOPIX が5営業日で {metrics['ret_5d']:+.1%}")
    if dd <= th.regime_out_high:
        level = 控える; reasons.append(f"TOPIX が直近{th.regime_high_days}営業日の高値から {dd:+.1%}")
    if level == 通常:
        if metrics["ret_5d"] is not None and metrics["ret_5d"] <= th.regime_warn_5d:
            level = 慎重; reasons.append(f"TOPIX が5営業日で {metrics['ret_5d']:+.1%}")
        if dd <= th.regime_warn_high:
            level = 慎重; reasons.append(f"TOPIX が高値から {dd:+.1%}")
        if len(breadth) >= th.regime_warn_themes:
            level = 慎重; reasons.append(f"テーマの {len(breadth)} 個が5日で {th.regime_theme_down:.0%} 以下")
    elif len(breadth) >= th.regime_warn_themes:
        reasons.append(f"テーマの {len(breadth)} 個が5日で {th.regime_theme_down:.0%} 以下(下げが広がっている)")

    # 底打ちの目印(株価が落ち着いたかだけ。再開してよい理由ではない)
    recent = rows[-th.regime_low_days:]
    low_date, low = min(recent, key=lambda x: x[1])
    since_low = sum(1 for d, _ in rows if d > low_date)
    rebound = last / low - 1
    last3 = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 3, len(closes))]
    notes: list[str] = []
    ok = True
    for cond, msg in ((since_low >= th.regime_calm_days, f"直近安値({low_date})から {since_low} 営業日"),
                      (rebound >= th.regime_rebound, f"安値から {rebound:+.1%} 戻した"),
                      (min(last3) >= th.regime_calm_day_drop, f"直近3日の最大の1日下落 {min(last3):+.1%}")):
        notes.append(("✓ " if cond else "✗ ") + msg)
        ok = ok and cond
    return {"as_of": day.isoformat(), "level": level, "reasons": reasons,
            "metrics": {k: (None if v is None else round(v, 4)) for k, v in metrics.items()},
            "stabilization": {"stabilized": ok, "low_date": low_date.isoformat(), "days_since_low": since_low,
                              "rebound_from_low": round(rebound, 4), "notes": notes,
                              "caveat": "株価が落ち着いたことは、投資を再開してよい理由にならない。下げた理由が解消されたかを別に確かめる"}}
