"""構造変化の見出しを、注目度に関係なく拾う(docs/DECISIONS.md D46)。

足切り(events/gate.py)は1件ごとの媒体数で判定する。ところが、産業の力関係を変える新しい報道
(例:エヌビディアが HBM のベースダイを TSMC に集約させる、DeepSeek がファーウェイのチップで事前学習へ)は、
日本語メディアでは最初、1〜2媒体の見出しとしてしか出ず、上位5%に届かない(2026-10-20 に発覚。
インテルの推論向けGPU「Dシリーズ」は10/14と10/17に「注目度・開示の種類のいずれの条件も満たさない」で落ちた)。

そこで、`knowledge/structural_terms.yaml` の「語の組み合わせ」(each_of:各グループの語のどれかを含む、を全グループで満たす)
に当たる見出しを、媒体数に関係なく別枠(forced_in)で通す。語の組み合わせにしてあるのは、HBM や半導体の語だけでは
毎日大量に当たり、別枠が膨らむため。同じ見出し(story_key)は一度出したら、以降は出さない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from assoc.config import REPO_ROOT
from assoc.events.burst import _pick, count_by_day, story_key

STRUCTURAL_PATH = REPO_ROOT / "knowledge" / "structural_terms.yaml"
LOOKBACK_DAYS = 1   # 当日に加えて前日の分も見る(前回のパックのあとの夜に取得した見出しを漏らさない)


@dataclass
class StructuralHit:
    group_id: str
    theme_id: str
    label: str
    samples: list[dict] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)   # 出した見出しの story_key(次回以降は出さない)


def load_groups(path: Path | None = None) -> dict[str, dict]:
    p = path or STRUCTURAL_PATH
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _matches(title: str, each_of: list[list[str]]) -> bool:
    t = title or ""
    return all(any(w in t for w in grp) for grp in each_of)


def detect_structural(con, day: date, groups: dict[str, dict] | None = None, *,
                      seen_keys: set[str] | None = None, max_samples: int = 6) -> list[StructuralHit]:
    groups = load_groups() if groups is None else groups
    seen_keys = seen_keys or set()
    out: list[StructuralHit] = []
    for gid, spec in groups.items():
        each_of = [list(g) for g in (spec.get("each_of") or []) if g]
        if not each_of:
            continue
        # 先頭のグループの語で絞り込み、残りは見出しの側で判定する
        _counts, items = count_by_day(con, each_of[0], day - timedelta(days=LOOKBACK_DAYS), day)
        hits = [i for d in sorted(items) for i in items[d] if _matches(i["title"], each_of)]
        fresh, keys = [], []
        for i in hits:
            k = story_key(i["title"])
            if k in seen_keys or k in keys:
                continue
            keys.append(k)
            fresh.append(i)
        if fresh:
            out.append(StructuralHit(group_id=gid, theme_id=spec.get("theme_id", ""), label=spec.get("label", gid),
                                     samples=_pick(fresh, max_samples), keys=keys))
    return out
