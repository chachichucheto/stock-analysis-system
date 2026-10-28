"""銘柄ごとの材料台帳(D50-A)。

イベント単位の判定(足切り・狙い撃ち)は『その日の1つの話』しか見ない。ある銘柄に別々の材料が数日のうちに重なる
(業績修正のあとに受注、不正アクセスのあとに第三者委員会と行政処分など)ことは、どの日にも1件ずつしか見えず、
気づけない。ここでは直近の日数分の見出し・開示を銘柄ごとに積み、種類の違う材料が重なった銘柄(か、同じ材料が
何日も続く銘柄)を入力パックに出す。クラスタリングはせず、見出しの種類(sniper.classify)と社名の特定(D48)だけを使うので軽い。
株価は使わない(ニュースが先)。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from assoc.events.cluster import media_of
from assoc.events.companies import CompanyIndex
from assoc.events.sniper import _STALE, classify

JST = ZoneInfo("Asia/Tokyo")
LOOKBACK_DAYS = 14
RECENT_DAYS = 2           # 台帳に載せるのは、直近この日数以内に新しい材料がある銘柄だけ(古い重なりを毎日出さない)
MIN_TYPES = 2             # 種類の違う材料がこの数以上重なれば載せる
MIN_RUN_DAYS = 3          # 同じ種類でも、この日数以上続けば載せる(GMOの不正アクセス型)
MIN_RUN_MEDIA = 3
MAX_LEDGER = 10
# 相手方も主役になる材料(提携・TOB・M&A・受注は、見出しに出た会社すべてに関係する)
_COUNTERPARTY = frozenset({"提携", "TOB", "M&A", "受注・契約"})
_SELF_RELEASE = re.compile(r"PR\s?TIMES|PRTIMES", re.I)


@dataclass
class Material:
    type: str
    weight: int
    days: set[str] = field(default_factory=set)
    media: set[str] = field(default_factory=set)
    articles: int = 0
    disclosure: bool = False
    title: str = ""
    url: str = ""
    last_day: str = ""


def _subject_codes(index: CompanyIndex, title: str, label: str) -> list[str]:
    out: list[str] = []
    for m in index.find(title):
        codes = index.subject_codes(m.key)
        if len(codes) == 1 and codes[0] not in out:
            out.append(codes[0])
    return out if label in _COUNTERPARTY else out[:1]


def collect_materials(con, index: CompanyIndex, day: date,
                      lookback: int = LOOKBACK_DAYS) -> dict[str, dict[str, Material]]:
    """day までの直近 lookback 日の見出し・開示から、銘柄ごと・種類ごとの材料を集める。"""
    start = datetime.combine(day - timedelta(days=lookback - 1), time.min, tzinfo=JST)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=JST)
    by_code: dict[str, dict[str, Material]] = defaultdict(dict)

    def add(code: str, label: str, weight: int, d: str, medium: str, title: str, url: str, disclosure: bool):
        m = by_code[code].setdefault(label, Material(label, weight))
        m.days.add(d)
        if medium:
            m.media.add(medium)
        m.articles += 1
        m.disclosure = m.disclosure or disclosure
        # 代表は最も新しい日の最初の見出し(開示があれば開示を優先)
        if d > m.last_day or (disclosure and not m.disclosure):
            m.last_day, m.title, m.url = d, title, url

    rows = con.execute("SELECT title, source, summary, url, first_observed_at FROM news_item "
                       "WHERE first_observed_at >= ? AND first_observed_at < ?", [start, end]).fetchall()
    for title, source, summary, url, obs in rows:
        cls = classify(title or "")
        if not cls:
            continue
        stale = _STALE.search(title)
        if stale and int(stale.group(1)) < obs.year:
            continue
        label, w = cls
        medium = media_of(source or "", summary or "")
        if _SELF_RELEASE.search(source or ""):
            medium = "(自社の発表)"
        d = obs.astimezone(JST).date().isoformat()
        for code in _subject_codes(index, title, label):
            add(code, label, w, d, medium, title, url or "", False)
    rows = con.execute("SELECT title, code, url, first_observed_at FROM disclosure "
                       "WHERE first_observed_at >= ? AND first_observed_at < ?", [start, end]).fetchall()
    for title, code, url, obs in rows:
        cls = classify(title or "")
        if not cls or not code:
            continue
        label, w = cls
        add(str(code), label, w, obs.astimezone(JST).date().isoformat(), "(本人の開示)", title, url or "", True)
    return by_code


def recent_materials(by_code: dict[str, dict[str, Material]], code: str) -> list[dict]:
    """その銘柄の直近の材料を1行ずつ(関連銘柄に『その銘柄自身の材料』を添えるため)。"""
    return [{"type": m.type, "last_day": m.last_day, "headline": m.title[:60], "own_disclosure": m.disclosure}
            for m in sorted(by_code.get(code, {}).values(), key=lambda m: (-m.weight, m.last_day))]


def build_ledger(con, index: CompanyIndex, day: date, *, liquid: set[str] | None, names: dict[str, str],
                 lookback: int = LOOKBACK_DAYS, limit: int = MAX_LEDGER,
                 exclude_codes: set[str] = frozenset(),
                 by_code: dict[str, dict[str, Material]] | None = None) -> list[dict]:
    """day までの直近 lookback 日で、材料が重なった(か続いている)銘柄。"""
    if by_code is None:
        by_code = collect_materials(con, index, day, lookback)
    recent_from = (day - timedelta(days=RECENT_DAYS - 1)).isoformat()
    out = []
    for code, mats in by_code.items():
        if code in exclude_codes or (liquid is not None and code not in liquid):
            continue
        if max(m.last_day for m in mats.values()) < recent_from:
            continue
        run = [m for m in mats.values() if len(m.days) >= MIN_RUN_DAYS and len(m.media) >= MIN_RUN_MEDIA]
        # 種類の違う材料でも、同じ日にまとめて出た開示(業績修正と配当修正など)は『重なり』にしない。日をまたいだものだけ
        spans_days = len({d for m in mats.values() for d in m.days}) >= 2
        if not run and not (len(mats) >= MIN_TYPES and spans_days):
            continue
        score = sum(m.weight for m in mats.values()) + len(run)
        out.append((score, code, mats))
    out.sort(key=lambda x: (-x[0], x[1]))
    result = []
    for score, code, mats in out[:limit]:
        items = sorted(mats.values(), key=lambda m: (-m.weight, m.last_day), reverse=False)
        result.append({
            "code": code, "company_name": names.get(code, ""), "score": score,
            "reason": ("種類の違う材料が重なっている" if len(mats) >= MIN_TYPES else "同じ材料が続いている"),
            "materials": [{"type": m.type, "days": sorted(m.days), "articles": m.articles,
                           "media_count": len([x for x in m.media if not x.startswith("(")]),
                           "own_disclosure": m.disclosure, "headline": m.title[:80], "url": m.url}
                          for m in sorted(items, key=lambda m: (-m.weight, m.last_day))],
        })
    return result
