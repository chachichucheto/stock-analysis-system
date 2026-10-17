"""テーマ別の「見出しの件数」の急増の検知(docs/DECISIONS.md D37)。

足切り(events/gate.py)は1件ごとの注目度で判定する。別々の会社の不正アクセスが連日報じられるような話題は、
1件ずつは媒体数が1で足切りを通らない(2026-10-07 に発覚)。そこで、テーマごとに辞書の語を含む見出しを
日ごとに数え、普段より急に増えたテーマを別枠で通す。辞書は `knowledge/theme_triggers.yaml`。

判定(1日ごと)
  - 件数が `min_count` 以上で、
  - 過去(最長 `burst_lookback_days` 日)の日別件数の中央値の `ratio` 倍以上なら「増えた日」。
  - 比べる履歴が `burst_early_days`(4日)以上あれば、14日に満たない間も同じ判定をする(倍率だけ `burst_early_ratio_mult` 倍に厳しくする)。
    履歴が4日未満の間は、辞書に `cold_start_count` を書いたテーマだけ、その件数以上で「増えた日」とする。
    14日に満たない間は、`cold_start_count` を書いたテーマは、その件数以上でも「増えた日」とする。
  - 当日が「増えた日」、または直近 `burst_recent_days` 日のうち `burst_persist_days` 日以上が「増えた日」なら、急増とする
    (週末に件数が落ちても、連日の報道が途切れたとは見なさない)。
数えるのは、取得した日(日本時間)別の、同じ見出しを1件にまとめた件数。初回の収集で取り込んだ古い記事
(公開が取得の2日より前)は数えない。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

from assoc.config import REPO_ROOT
from assoc.events.cluster import media_of
from assoc.timeutil import JST, jst_date

TRIGGERS_PATH = REPO_ROOT / "knowledge" / "theme_triggers.yaml"
_BACKFILL_DAYS = 2   # 公開が取得より2日以上前の記事は、初回の収集の取りこぼし分として数えない


@dataclass
class ThemeBurst:
    theme_id: str
    today_count: int
    baseline: float | None            # 過去の日別件数の中央値(履歴が足りなければ None)
    ratio: float | None
    elevated_days: int                # 直近の日数のうち「増えた日」の数
    daily: list[tuple[str, int]]      # 直近の日別件数 [(日付, 件数)](古い順)
    cold_start: bool
    samples: list[dict] = field(default_factory=list)   # 当日の代表の見出し(新しい順)


def load_triggers(path: Path | None = None) -> dict[str, dict]:
    p = path or TRIGGERS_PATH
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


# 末尾の媒体名(「 - ITmedia」「 | 朝日新聞」)。区切りは前後に空白のあるハイフン類か、縦線。
# 空白なしの「ー」(長音)や「－」を区切りにすると、「ローバー3号機…」のようなカタカナ語を途中で切ってしまう(D39で修正)
_SUFFIX = re.compile(r"\s+[-－–—]\s+[^-－–—|｜]{1,20}$|\s*[|｜]\s*[^|｜]{1,20}$")
_PAREN = re.compile(r"[（(][^）)]{1,12}[）)]$")
_NOISE = re.compile(r"[\s、。「」『』【】]+")


def story_key(title: str) -> str:
    """同じ記事の転載(末尾の媒体名の違い)を1件にまとめるための鍵。"""
    t = _PAREN.sub("", _SUFFIX.sub("", title or ""))
    return _NOISE.sub("", t)[:22]


def _rows(con, words: list[str], start: date, end: date):
    """start 〜 end(日本時間の日付、両端を含む)に取得した、語を含む見出し。"""
    lo = datetime.combine(start, time.min, tzinfo=JST)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=JST)
    cond = " OR ".join(["title LIKE ?"] * len(words))
    sql = (f"SELECT title, url, source, summary, first_observed_at, published_at FROM news_item "
           f"WHERE first_observed_at >= ? AND first_observed_at < ? AND ({cond}) ORDER BY first_observed_at DESC")
    return con.execute(sql, [lo, hi, *[f"%{w}%" for w in words]]).fetchall()


def count_by_day(con, words: list[str], start: date, end: date) -> tuple[dict[date, int], dict[date, list[dict]]]:
    seen: dict[date, set[str]] = {}
    items: dict[date, list[dict]] = {}
    for title, url, source, summary, observed, published in _rows(con, words, start, end):
        if published is not None and published < observed - timedelta(days=_BACKFILL_DAYS):
            continue
        d = jst_date(observed)
        key = story_key(title)
        if key in seen.setdefault(d, set()):
            continue
        seen[d].add(key)
        items.setdefault(d, []).append({"title": title, "url": url, "media": media_of(source or "", summary or ""),
                                        "observed_at": observed.astimezone(JST).isoformat(timespec="minutes")})
    return {d: len(v) for d, v in seen.items()}, items


def _collected_days(con, start: date, end: date) -> list[date]:
    """ニュースを1件でも取得した日(収集が止まっていた日を「0件」と数えないため)。"""
    lo = datetime.combine(start, time.min, tzinfo=JST)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=JST)
    rows = con.execute("SELECT DISTINCT first_observed_at FROM news_item WHERE first_observed_at >= ? "
                       "AND first_observed_at < ?", [lo, hi]).fetchall()
    return sorted({jst_date(r[0]) for r in rows})


def _elevated(count: int, base: float | None, spec: dict, th, history_days: int) -> bool:
    min_count = int(spec.get("min_count", th.burst_min_count))
    cold = spec.get("cold_start_count")
    if history_days < th.burst_min_history_days and cold is not None and count >= max(int(cold), min_count):
        return True                                       # 履歴が短い間の、絶対値の基準(書いたテーマだけ)
    if history_days < th.burst_early_days or base is None:
        return False                                      # 比べる日が少なすぎる
    ratio = float(spec.get("ratio", th.burst_ratio))
    if history_days < th.burst_min_history_days:
        ratio *= th.burst_early_ratio_mult                # 履歴が短い間は、中央値が不安定なので、倍率を厳しくする
    return count >= min_count and count >= ratio * max(base, 1.0)


def detect_bursts(con, day: date, th, triggers: dict[str, dict] | None = None,
                  max_samples: int = 10) -> list[ThemeBurst]:
    """day(日本時間)の時点で急増しているテーマを返す。急増していないテーマは返さない。"""
    triggers = load_triggers() if triggers is None else triggers
    lookback_start = day - timedelta(days=th.burst_lookback_days)
    collected = [d for d in _collected_days(con, lookback_start, day) if d <= day]
    out: list[ThemeBurst] = []
    for tid, spec in triggers.items():
        words = list(spec.get("words") or [])
        if not words:
            continue
        counts, items = count_by_day(con, words, lookback_start, day)
        series = {d: counts.get(d, 0) for d in collected}

        def baseline_for(d: date) -> tuple[float | None, int]:
            prior = [series[x] for x in collected if x < d][-th.burst_lookback_days:]
            return (statistics.median(prior) if prior else None), len(prior)

        recent = [d for d in collected if d > day - timedelta(days=th.burst_recent_days)]
        flags = {}
        for d in recent:
            base, n = baseline_for(d)
            flags[d] = _elevated(series[d], base, spec, th, n)
        base, n = baseline_for(day)
        elevated_days = sum(flags.values())
        if not (flags.get(day) or elevated_days >= th.burst_persist_days):
            continue
        today = series.get(day, 0)
        out.append(ThemeBurst(
            theme_id=tid, today_count=today, baseline=base,
            ratio=None if not base else round(today / max(base, 1.0), 2),
            elevated_days=elevated_days, daily=[(d.isoformat(), series[d]) for d in recent],
            cold_start=n < th.burst_min_history_days, samples=_pick(items.get(day, []), max_samples)))
    return out


def _pick(items: list[dict], n: int) -> list[dict]:
    """代表の見出し。直接開ける URL(Google News の中継でないもの)を先に、同じ媒体は重ねすぎない。"""
    items = [i for i in items if i["media"] != "PRTIMES"] + [i for i in items if i["media"] == "PRTIMES"]  # 販促は最後
    direct = [i for i in items if "news.google.com" not in (i["url"] or "")]
    rest = [i for i in items if "news.google.com" in (i["url"] or "")]
    picked: list[dict] = []
    per_media: dict[str, int] = {}
    for i in direct + rest:
        if per_media.get(i["media"], 0) >= 2:
            continue
        per_media[i["media"]] = per_media.get(i["media"], 0) + 1
        picked.append(i)
        if len(picked) >= n:
            break
    return picked
