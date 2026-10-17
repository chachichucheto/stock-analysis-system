"""辞書に無い話題を拾う:「急に増えた語」の検知(docs/DECISIONS.md D39)。

events/burst.py は、辞書(`knowledge/theme_triggers.yaml`)にあるテーマの件数の急増しか見ない。辞書に無い話題が連日
報じられても、気づけない。そこで、見出しから日ごとに語(3〜8文字の文字列)を数え、**その日に急に増えた語**を
辞書とは無関係に拾う。

  1. 取得した日(日本時間)別に、見出しの文字列(漢字・カタカナ・英数字の連続のうち、3〜8文字の部分文字列)を、
     「その語を含む記事の数(同じ見出しは1件)」で数える。
  2. 当日の記事数が `terms_min_count` 以上で、過去の日別記事数の中央値の `terms_ratio` 倍以上(過去に出ていなければ
     中央値を `terms_floor` とみなす)なら候補。比べる日が `terms_min_days` 日に満たない間は検知しない。
  3. 一般語(記事が毎日大量に出る語)は、中央値が高いので倍率が出ず、自然に落ちる。さらに、語が記事の
     `terms_max_share` を超えて含まれる(=どの話題にも出る語)ものは除く。
  4. 包含関係にある候補(「不正アクセス」と「正アクセス」など)は、記事数が最大のものだけを残す。
  5. すでに辞書のテーマの語に当たるものは、`covered` として分ける(辞書側の検知が見ているため)。

これは判断ではなく**気づくための仕組み**。語が何を意味するかは、夜の手順(LLM)が代表の見出しから読む。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from assoc.events.burst import _BACKFILL_DAYS, _collected_days, load_triggers, story_key
from assoc.timeutil import JST, jst_date

_RUN = re.compile(r"[一-龥ァ-ヶーA-Za-z0-9]{3,}")
_NGRAM_MIN, _NGRAM_MAX = 3, 8
# 語として意味が無い(日付・数字・助数詞・媒体名・定型句)ものを除くための語尾・語頭
_NUMERIC = re.compile(r"^[0-9０-９A-Za-z]+$|[0-9０-９]{2,}|[0-9０-９][年月日時分円万億]|[年月日時分円万億][0-9０-９]|^[年月日時分円万億台前後]")
_STOP_SUBSTR = ("ニュース", "速報", "ついて", "による", "について", "として", "までに", "ため", "こと", "する", "された",
                "されて", "している", "なった", "という", "ている", "から", "まで", "など", "株式会社", "お知らせ",
                "開催", "提供", "開始", "発表", "決定", "公開", "記者", "日本経済", "朝日新聞", "読売", "毎日新聞",
                "報告書", "保有割合", "大量保有報告書", "前後", "万円台", "株式")


@dataclass
class EmergingTerm:
    term: str
    today_count: int
    baseline: float
    ratio: float
    covered_by: list[str] = field(default_factory=list)   # すでにこの語を含む辞書のテーマ
    samples: list[dict] = field(default_factory=list)


# 定型語の3文字の部分も除く(「株式会社」の断片「券株式会」などを語にしないため)
_STOP_TRIGRAMS = {w[i:i + 3] for w in _STOP_SUBSTR if len(w) >= 4 for i in range(len(w) - 2)}


def _ngrams(title: str) -> set[str]:
    out: set[str] = set()
    for run in _RUN.findall(title or ""):
        n = len(run)
        for size in range(_NGRAM_MIN, min(_NGRAM_MAX, n) + 1):
            for i in range(n - size + 1):
                g = run[i:i + size]
                if (g[0] not in "ーンッァィゥェォャュョ" and not _NUMERIC.search(g) and not any(w in g or (len(w) >= 4 and g in w) for w in _STOP_SUBSTR)
                        and not any(t in g for t in _STOP_TRIGRAMS)):
                    out.add(g)
    return out


def _stories(con, start: date, end: date) -> dict[date, list[tuple[str, str, str, str]]]:
    """日(日本時間)別の (見出し, URL, 媒体, 取得時刻)。同じ話は1件。初回の収集の古い記事は除く。"""
    from assoc.events.cluster import media_of
    lo = datetime.combine(start, time.min, tzinfo=JST)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=JST)
    rows = con.execute("SELECT title, url, source, summary, first_observed_at, published_at FROM news_item "
                       "WHERE first_observed_at >= ? AND first_observed_at < ? ORDER BY first_observed_at DESC",
                       [lo, hi]).fetchall()
    out: dict[date, list[tuple[str, str, str, str]]] = {}
    seen: dict[date, set[str]] = {}
    for title, url, source, summary, observed, published in rows:
        if published is not None and published < observed - timedelta(days=_BACKFILL_DAYS):
            continue
        d = jst_date(observed)
        k = story_key(title)
        if k in seen.setdefault(d, set()):
            continue
        seen[d].add(k)
        out.setdefault(d, []).append((title, url or "", media_of(source or "", summary or ""),
                                      observed.astimezone(JST).isoformat(timespec="minutes")))
    return out


def detect_emerging_terms(con, day: date, th, triggers: dict[str, dict] | None = None,
                          limit: int = 15, matcher=None) -> list[EmergingTerm]:
    """day に急に増えた語を返す(増え方の大きい順)。比べる日が足りなければ空。

    matcher(見出し → 0〜3。events/relevance.py)を渡すと、その語を含む見出しの `terms_min_relevant_share` 以上に
    投資との関係(社名・テーマの語・株価に効く事象の語)が無い語は除く(事件・スポーツ・芸能の話題で鳴らさないため)。"""
    triggers = load_triggers() if triggers is None else triggers
    start = day - timedelta(days=th.burst_lookback_days)
    collected = [d for d in _collected_days(con, start, day) if d <= day]
    prior_days = [d for d in collected if d < day]
    if len(prior_days) < th.terms_min_days:
        return []
    stories = _stories(con, start, day)
    today_titles = stories.get(day, [])
    if not today_titles:
        return []
    df_today: dict[str, int] = {}
    for t, *_ in today_titles:
        for g in _ngrams(t):
            df_today[g] = df_today.get(g, 0) + 1
    cands = [g for g, n in df_today.items() if n >= th.terms_min_count
             and n <= th.terms_max_share * len(today_titles)]
    if not cands:
        return []
    cset = set(cands)
    per_day: dict[str, list[int]] = {g: [] for g in cands}
    for d in prior_days:
        cnt: dict[str, int] = {}
        for t, *_ in stories.get(d, []):
            for g in _ngrams(t) & cset:
                cnt[g] = cnt.get(g, 0) + 1
        for g in cands:
            per_day[g].append(cnt.get(g, 0))
    rows = []
    for g in cands:
        base = statistics.median(per_day[g])
        ratio = df_today[g] / max(base, th.terms_floor)
        if ratio >= th.terms_ratio:
            rows.append((g, df_today[g], base, ratio))
    # 断片を除く:より長い語に含まれ、その長い語の記事数が5割以上ある語は、その語の一部にすぎない
    rows = [r for r in rows if not any(r[0] != h[0] and r[0] in h[0] and h[1] >= 0.5 * r[1] for h in rows)]
    rows.sort(key=lambda r: (-r[1], -len(r[0])))
    kept: list[tuple[str, int, float, float]] = []
    for g, n, base, ratio in rows:                      # 重なる語(3文字以上の共通部分がある)は、記事数が最大のものだけ残す
        gs = {g[i:i + 3] for i in range(len(g) - 2)}
        if any(gs & {k[0][i:i + 3] for i in range(len(k[0]) - 2)} for k in kept):
            continue
        if matcher is not None:
            titles = [t for t, *_ in today_titles if g in t]
            rel = sum(1 for t in titles if matcher(t) >= 1) / max(len(titles), 1)
            if rel < th.terms_min_relevant_share:
                continue
        kept.append((g, n, base, ratio))
    kept.sort(key=lambda r: -r[3] * (r[1] ** 0.5))
    # 同じ記事から出た語は、1つにまとめる(「旭化成」「再逮捕」「元社員」は、同じ数本の記事の別の語にすぎない)
    uniq: list[tuple[str, int, float, float]] = []
    seen_sets: list[set[str]] = []
    for r in kept:
        ts = {t for t, *_ in today_titles if r[0] in t}
        if any(len(ts & o) >= 0.6 * min(len(ts), len(o)) for o in seen_sets):
            continue
        uniq.append(r)
        seen_sets.append(ts)
    kept = uniq
    out: list[EmergingTerm] = []
    for g, n, base, ratio in kept[:limit]:
        covered = [tid for tid, spec in triggers.items() if any(w in g or g in w for w in spec.get("words") or [])]
        samples = [{"title": t, "url": u, "media": m, "observed_at": o} for t, u, m, o in today_titles if g in t][:5]
        out.append(EmergingTerm(term=g, today_count=n, baseline=round(base, 1), ratio=round(ratio, 1),
                                covered_by=covered, samples=samples))
    return out
