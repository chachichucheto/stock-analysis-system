"""株価の大きな動きから、足切りの枠外でイベントを作る(docs/DECISIONS.md D40)。

足切り(gate.py)も件数の急増(burst.py)も「報道の量」を見る。ところが、相場全体を動かすニュースは、
媒体ごとに見出しの言い回しが違って1件にまとまらず(媒体数が1〜2)、辞書の語にも当たらず、全部落ちる
(2026-10-17:日経平均3000円安・米CPI上振れ・AI半導体株の急落が、すべて「注目度の条件を満たさない」で落ちた)。
そこで、報道の量ではなく**株価そのもの**を見る。

  - テーマ別:代表銘柄の5日の騰落率の中央値が、TOPIX を `shock_theme_excess_5d` 以上、上回る/下回る
  - 地合い:TOPIX が直近5営業日のどこかで1日 `shock_topix_day` 以上下げた、または5日で `shock_topix_5d` 以上下げた

動いた原因は、見出しの語(knowledge/market_shock.yaml、テーマは theme_triggers.yaml)で集めて代表の見出しにする。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from assoc.config import REPO_ROOT
from assoc.events.burst import _pick, count_by_day, load_triggers

MARKET_WORDS_PATH = REPO_ROOT / "knowledge" / "market_shock.yaml"
_LOOKBACK_DAYS = 3   # 代表の見出しを集める日数(当日を含めて、日付で4日分)


@dataclass
class PriceShock:
    shock_id: str                 # "market" または テーマID
    kind: str                     # "market" | "theme"
    summary: str                  # 株価の動きの説明(見出しに使う)
    metrics: dict = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)


def load_market_words(path: Path | None = None) -> list[str]:
    p = path or MARKET_WORDS_PATH
    if not p.exists():
        return []
    return list((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("words") or [])


def topix_stats(topix_df, window: int = 5) -> dict | None:
    """直近 window 営業日の、TOPIX の最悪の1日と、期間の騰落率。"""
    if topix_df is None or topix_df.empty:
        return None
    s = topix_df["close"].dropna().reset_index(drop=True)
    if len(s) < window + 1:
        return None
    rets = (s / s.shift(1) - 1).iloc[-window:]
    return {"worst_day": float(rets.min()), "ret_window": float(s.iloc[-1] / s.iloc[-1 - window] - 1)}


def _headlines(con, words: list[str], day: date, max_samples: int) -> list[dict]:
    if not words:
        return []
    counts, items = count_by_day(con, words, day - timedelta(days=_LOOKBACK_DAYS), day)
    flat = [i for d in sorted(items, reverse=True) for i in items[d]]
    return _pick(flat, max_samples)


def detect_price_shocks(con, day: date, th, theme_returns: dict, topix_df=None, *,
                        themes: dict[str, str] | None = None, triggers: dict | None = None,
                        market_words: list[str] | None = None, max_samples: int = 12) -> list[PriceShock]:
    triggers = load_triggers() if triggers is None else triggers
    market_words = load_market_words() if market_words is None else market_words
    themes = themes or {}
    out: list[PriceShock] = []

    ts = topix_stats(topix_df)
    if ts and (ts["worst_day"] <= -th.shock_topix_day or ts["ret_window"] <= -th.shock_topix_5d):
        out.append(PriceShock(
            "market", "market",
            f"TOPIX が直近5営業日で最悪の1日 {ts['worst_day']:+.1%}、5日で {ts['ret_window']:+.1%}",
            ts, _headlines(con, market_words, day, max_samples)))

    topix_5d = theme_returns.get("topix_5d") or 0.0
    for tid, t in (theme_returns.get("themes") or {}).items():
        m5 = t.get("median_5d")
        if m5 is None or (t.get("n_with_data") or 0) < th.shock_min_stocks:
            continue
        excess = m5 - topix_5d
        if abs(excess) < th.shock_theme_excess_5d:
            continue
        words = list((triggers.get(tid) or {}).get("words") or [])
        samples = _headlines(con, words, day, max_samples)
        if len(samples) < 4:   # テーマの語で集まらないときだけ、地合いの語で補う
            samples += _headlines(con, market_words, day, max_samples - len(samples))
        seen, uniq = set(), []
        for s in samples:
            if s["url"] not in seen:
                seen.add(s["url"])
                uniq.append(s)
        out.append(PriceShock(
            tid, "theme",
            f"代表銘柄の5日の騰落率の中央値 {m5:+.1%}(TOPIX {topix_5d:+.1%}、超過 {excess:+.1%})",
            {"median_5d": m5, "topix_5d": topix_5d, "excess_5d": excess}, uniq[:max_samples]))
    return out
