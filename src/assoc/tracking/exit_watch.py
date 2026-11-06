"""撤退・縮小の目印(D51)。

売りのサイン(sell_signals.py)と継続性チェック(health.py)は、どちらも**シナリオに結びついている**。
シナリオが「織り込み完了」で終わると監視から外れ、TOPIX との差(超過)で測るので、**相場全体が下がる日は反応しない**。
2026-11 の急落では、本命にした半導体株(ディスコ・アドバンテスト・イビデンなど)が1か月で -30%超になっても、
売りのサインは「なし」だった。

ここでは次の2つを、シナリオの状態に関係なく、**株価の絶対の動き**で見る。
1. 過去に本命とした銘柄(と、保有銘柄の一覧 `knowledge/holdings.yaml`)の、本命にした日からの騰落と高値からの下落
2. テーマ(代表銘柄の中央値)の5日・1か月の下落

出力は「目印」であり、判断ではない。夜の手順(LLM)が原因を調べて、撤退・縮小・警戒・継続を決める。
閾値は `config.Thresholds` の exit_*(暫定)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from assoc.config import REPO_ROOT, Thresholds

撤退検討 = "撤退検討"
縮小検討 = "縮小検討"
警戒 = "警戒"
HOLDINGS_PATH = REPO_ROOT / "knowledge" / "holdings.yaml"
_ORDER = {撤退検討: 0, 縮小検討: 1, 警戒: 2}


@dataclass
class StockExit:
    code: str
    name: str
    level: str
    reasons: list[str]
    metrics: dict[str, float | None]
    sources: list[str] = field(default_factory=list)      # 本命 / 保有


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)


def stock_level(since_pick: float | None, from_peak: float | None, ret_5d: float | None,
                market_5d: float | None, th: Thresholds) -> tuple[str, list[str]]:
    """銘柄の絶対の動きから目印を決める。相場全体が崩れている日は、基準を少し浅くする(逃げ遅れを避ける)。"""
    stress = market_5d is not None and market_5d <= th.exit_market_stress_5d
    k = th.exit_stress_factor if stress else 1.0
    reasons: list[str] = []
    level = ""

    def up(new: str) -> None:
        nonlocal level
        if not level or _ORDER[new] < _ORDER[level]:
            level = new

    if since_pick is not None:
        if since_pick <= th.exit_since_pick_out * k:
            up(撤退検討); reasons.append(f"本命にした日から {since_pick:+.0%}")
        elif since_pick <= th.exit_since_pick_cut * k:
            up(縮小検討); reasons.append(f"本命にした日から {since_pick:+.0%}")
    if from_peak is not None:
        if from_peak <= th.exit_from_peak_out * k:
            up(撤退検討); reasons.append(f"本命にしたあとの高値から {from_peak:+.0%}")
        elif from_peak <= th.exit_from_peak_cut * k:
            up(縮小検討); reasons.append(f"本命にしたあとの高値から {from_peak:+.0%}")
    if ret_5d is not None and ret_5d <= th.exit_ret_5d_warn * k:
        up(縮小検討 if ret_5d <= th.exit_ret_5d_cut * k else 警戒)
        reasons.append(f"直近5営業日 {ret_5d:+.0%}")
    if level and stress:
        reasons.append(f"相場全体も崩れている(TOPIX 5日 {market_5d:+.1%})ので、基準を浅くして判定")
    return level, reasons


def _close_series(prices: pd.DataFrame, start: date | None, day: date) -> list[tuple[date, float]]:
    if prices is None or prices.empty:
        return []
    rows = [(d, float(c)) for d, c in zip(prices["date"], prices["close"])
            if d <= day and (start is None or d >= start) and c == c]
    rows.sort(key=lambda x: x[0])
    return rows


def stock_metrics(prices: pd.DataFrame, start: date | None, day: date) -> dict[str, float | None]:
    """start(本命にした日)以降の終値から、絶対の騰落を計算する。start が無ければ直近20営業日を使う。"""
    allrows = _close_series(prices, None, day)
    out: dict[str, float | None] = {"since_pick": None, "from_peak": None, "ret_5d": None, "ret_20d": None, "last_date": None}
    if not allrows:
        return out
    out["last_date"] = allrows[-1][0].isoformat()
    last = allrows[-1][1]
    if len(allrows) > 5:
        out["ret_5d"] = last / allrows[-6][1] - 1
    if len(allrows) > 20:
        out["ret_20d"] = last / allrows[-21][1] - 1
    base_rows = _close_series(prices, start, day) if start else allrows[-21:]
    if base_rows:
        out["since_pick"] = last / base_rows[0][1] - 1
        peak = max(c for _, c in base_rows)
        out["from_peak"] = last / peak - 1
    return {k: (_r(v) if isinstance(v, float) else v) for k, v in out.items()}


def holdings_codes(path: Path = HOLDINGS_PATH) -> dict[str, str]:
    """保有銘柄の一覧(任意)。`codes:` の下に `- 6857 # アドバンテスト` の形で書く。"""
    import yaml
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    out: dict[str, str] = {}
    for item in data.get("codes") or []:
        if isinstance(item, dict):
            out[str(item.get("code"))] = str(item.get("name") or "")
        else:
            out[str(item)] = ""
    return out


def stock_exits(picks: list[dict[str, Any]], held: dict[str, str], loader, topix: pd.DataFrame, day: date,
                names: dict[str, str], th: Thresholds) -> list[StockExit]:
    """本命にした銘柄(追跡期間内。シナリオの状態は問わない)と保有銘柄の撤退・縮小の目印。"""
    market_5d = None
    trows = _close_series(topix, None, day)
    if len(trows) > 5:
        market_5d = trows[-1][1] / trows[-6][1] - 1
    horizon = day - timedelta(days=th.exit_track_days)
    # 同じ銘柄を複数のシナリオで本命にしていたら、最初の日を起点にする
    first: dict[str, date] = {}
    for p in picks:
        if p.get("event") != "first_pick":
            continue
        d = date.fromisoformat(p["first_pick_date"])
        if d < horizon:
            continue
        code = p["code"]
        first[code] = min(first.get(code, d), d)
    out: list[StockExit] = []
    for code in dict.fromkeys(list(first) + list(held)):
        try:
            prices = loader.get(code)
        except Exception:
            continue
        m = stock_metrics(prices, first.get(code), day)
        level, reasons = stock_level(m["since_pick"], m["from_peak"], m["ret_5d"], market_5d, th)
        if not level:
            continue
        # 個別の話か、相場全体の下げか(TOPIX との差)
        if market_5d is not None and m["ret_5d"] is not None:
            m["ret_5d_vs_topix"] = _r(m["ret_5d"] - market_5d)
        srcs = (["本命"] if code in first else []) + (["保有"] if code in held else [])
        out.append(StockExit(code, names.get(code) or held.get(code) or code, level, reasons, m, srcs))
    out.sort(key=lambda s: (_ORDER[s.level], s.metrics.get("since_pick") or 0.0))
    return out


def theme_exits(tr: dict[str, Any], themes: dict[str, str], code_to_sources: dict[str, list[str]],
                th: Thresholds) -> list[dict[str, Any]]:
    """テーマ(代表銘柄の中央値)の下落。本命・保有の銘柄が含まれるテーマは `affected` に出す。"""
    topix_5d = tr.get("topix_5d")
    out: list[dict[str, Any]] = []
    for tid, t in (tr.get("themes") or {}).items():
        if not t.get("n_with_data"):
            continue
        m5, m1 = t.get("median_5d"), t.get("median_1m")
        reasons: list[str] = []
        level = ""

        def up(new: str) -> None:
            nonlocal level
            if not level or _ORDER[new] < _ORDER[level]:
                level = new

        if m1 is not None and m1 <= th.exit_theme_1m_out and m5 is not None and m5 <= th.exit_theme_5d_cut:
            up(撤退検討); reasons.append(f"1か月 {m1:+.0%} に続いて5日でも {m5:+.0%}(下げが続いている)")
        elif (m5 is not None and m5 <= th.exit_theme_5d_cut) or (m1 is not None and m1 <= th.exit_theme_1m_cut):
            up(縮小検討)
            if m5 is not None and m5 <= th.exit_theme_5d_cut:
                reasons.append(f"5日 {m5:+.0%}")
            if m1 is not None and m1 <= th.exit_theme_1m_cut:
                reasons.append(f"1か月 {m1:+.0%}")
        elif m5 is not None and m5 <= th.exit_theme_5d_warn:
            up(警戒); reasons.append(f"5日 {m5:+.0%}")
        if not level:
            continue
        affected = [{"code": s["code"], "name": s["name"], "sources": code_to_sources[s["code"]]}
                    for s in t.get("stocks", []) if s["code"] in code_to_sources]
        out.append({"theme_id": tid, "name": themes.get(tid, tid), "level": level, "reasons": reasons,
                    "median_5d": _r(m5), "median_1m": _r(m1), "median_3m": _r(t.get("median_3m")),
                    "topix_5d": _r(topix_5d), "n": t.get("n_with_data"),
                    "excess_5d": _r(m5 - topix_5d) if (m5 is not None and topix_5d is not None) else None,
                    "affected_holdings": affected})
    out.sort(key=lambda x: (_ORDER[x["level"]], -(len(x["affected_holdings"])), x["median_5d"] or 0.0))
    return out


def build_exit_watch(picks: list[dict[str, Any]], held: dict[str, str], loader, topix: pd.DataFrame, day: date,
                     names: dict[str, str], tr: dict[str, Any], themes: dict[str, str],
                     th: Thresholds) -> dict[str, Any]:
    picks = list(picks)         # 記録の読み出しは1回しか使えない形式のことがあるため、リストにして2回使う
    stocks = stock_exits(picks, held, loader, topix, day, names, th)
    horizon = day - timedelta(days=th.exit_track_days)
    code_sources: dict[str, list[str]] = {}
    for p in picks:
        if p.get("event") == "first_pick" and date.fromisoformat(p["first_pick_date"]) >= horizon:
            code_sources.setdefault(p["code"], ["本命"])
    for c in held:
        code_sources[c] = sorted(set(code_sources.get(c, []) + ["保有"]), key=["本命", "保有"].index)
    return {
        "as_of": day.isoformat(),
        "tracked_codes": len(code_sources),
        "stocks": [{"code": s.code, "name": s.name, "level": s.level, "reasons": s.reasons,
                    "metrics": s.metrics, "sources": s.sources} for s in stocks],
        "themes": theme_exits(tr, themes, code_sources, th),
    }
