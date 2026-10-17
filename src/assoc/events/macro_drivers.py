"""マクロの材料(米CPI・金利・為替・AIへの資金不安など)から、影響を受けるテーマを引く(docs/DECISIONS.md D41)。

背景:個別株のニュースは社名から関連銘柄を探せるが、マクロの材料は見出しに銘柄名が出ない。2026-10-17、日経平均3,318円安・
米CPI上振れ・AI関連の資金不安が出ても、『金利が上がれば銀行・保険』『円安なら輸出』『AIが売られれば他へ資金が移る』といった
連想が何も出なかった。そこで、
  - `knowledge/macro_map.yaml`:材料 → 影響先のテーマ(辞書。推測の起点で、結論ではない)
  - 直近の見出しで、どの材料が実際に出ているか(件数と代表の見出し)
  - 影響先のテーマの株価がすでにその方向に動いたか、まだ動いていない銘柄はどれか(株価で確かめる)
  - テーマ全体の資金の移動(5日の騰落率の順位)
を、入力パックに載せる。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import yaml

from assoc.config import REPO_ROOT
from assoc.events.burst import _pick, count_by_day

MACRO_MAP_PATH = REPO_ROOT / "knowledge" / "macro_map.yaml"
_LOOKBACK_DAYS = 2   # 当日を含めて日付で3日分


def load_macro_map(path: Path | None = None) -> dict[str, dict]:
    p = path or MACRO_MAP_PATH
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def theme_rotation(theme_returns: dict, names: dict[str, str] | None = None, *, min_excess: float = 0.04) -> dict:
    """テーマ別の5日の騰落率を TOPIX との差で並べる。資金がどこから、どこへ動いたかを見るための表。

    上がったテーマには、その中でまだ動いていない銘柄(出遅れ)も添える。"""
    names = names or {}
    topix = theme_returns.get("topix_5d") or 0.0
    rows = []
    for tid, t in (theme_returns.get("themes") or {}).items():
        m5 = t.get("median_5d")
        if m5 is None or not t.get("n_with_data"):
            continue
        rows.append({"theme_id": tid, "name": names.get(tid, tid), "median_5d": round(m5, 4),
                     "excess_5d": round(m5 - topix, 4), "n": t.get("n_with_data"),
                     "median_1m": None if t.get("median_1m") is None else round(t["median_1m"], 4),
                     "median_3m": None if t.get("median_3m") is None else round(t["median_3m"], 4),
                     "laggards": _laggards(t, m5) if m5 - topix >= min_excess else []})
    rows.sort(key=lambda r: r["excess_5d"], reverse=True)
    return {"topix_5d": round(topix, 4), "as_of": theme_returns.get("as_of"),
            "rising": [r for r in rows if r["excess_5d"] >= min_excess],
            "falling": [r for r in rows if r["excess_5d"] <= -min_excess][::-1],
            "flat_count": sum(1 for r in rows if abs(r["excess_5d"]) < min_excess)}


def _laggards(t: dict, theme_median: float) -> list[dict]:
    """上がったテーマの中で、5日の上昇がテーマの中央値の半分に満たない銘柄(連想が届いていない可能性)。"""
    out = []
    for s in t.get("stocks") or []:
        r5 = s.get("ret_5d")
        if r5 is not None and r5 < theme_median / 2:
            out.append({"code": s["code"], "name": s.get("name"), "ret_5d": round(r5, 4)})
    return out


def detect_macro_drivers(con, day: date, theme_returns: dict, macro_map: dict | None = None,
                         names: dict[str, str] | None = None, max_samples: int = 3) -> list[dict]:
    """直近の見出しに出ている材料と、その影響先のテーマの株価の反応。"""
    macro_map = load_macro_map() if macro_map is None else macro_map
    names = names or {}
    topix = theme_returns.get("topix_5d") or 0.0
    themes = theme_returns.get("themes") or {}
    out = []
    for did, spec in macro_map.items():
        words = list(spec.get("words") or [])
        if not words:
            continue
        counts, items = count_by_day(con, words, day - timedelta(days=_LOOKBACK_DAYS), day)
        hits = sum(counts.values())
        if hits < int(spec.get("min_count", 5)):
            continue
        flat = [i for d in sorted(items, reverse=True) for i in items[d]]
        effects = []
        for e in spec.get("effects") or []:
            t = themes.get(e["theme"]) or {}
            m5 = t.get("median_5d")
            excess = None if m5 is None else round(m5 - topix, 4)
            # 辞書の向き(up/down)どおりに株価がすでに動いたか。動いていない銘柄は『連想がまだ届いていない』候補
            moved = None if excess is None else (excess > 0.02 if e["dir"] == "up" else excess < -0.02)
            unmoved = []
            for s in t.get("stocks") or []:
                r5 = s.get("ret_5d")
                if r5 is None:
                    continue
                if (e["dir"] == "up" and r5 - topix < 0.02) or (e["dir"] == "down" and r5 - topix > -0.02):
                    unmoved.append({"code": s["code"], "name": s.get("name"), "ret_5d": round(r5, 4)})
            effects.append({"theme_id": e["theme"], "name": names.get(e["theme"], e["theme"]), "dir": e["dir"],
                            "why": e.get("why"), "excess_5d": excess, "already_moved": moved, "unmoved_stocks": unmoved})
        out.append({"driver_id": did, "name": spec.get("name", did), "headline_count_3d": hits,
                    "sample_headlines": [{"title": i["title"], "media": i["media"], "url": i["url"]}
                                         for i in _pick(flat, max_samples)],
                    "effects": effects})
    out.sort(key=lambda d: d["headline_count_3d"], reverse=True)
    return out
