"""NHK など報道機関の RSS 収集(docs/DESIGN.md §3)。

想定している形式
----------------
- RSS 2.0(NHK の `https://www.nhk.or.jp/rss/news/cat0.xml` など)。
  `<channel><item><title>…</title><link>…</link><description>…</description>
  <pubDate>Mon, 01 Jan 2026 09:00:00 +0900</pubDate></item></channel>`
- 文字コードは UTF-8 を想定(NHK の RSS は UTF-8)。他紙は Shift_JIS の場合があるため、
  取得時は `requests` の `response.content`(バイト列)を feedparser にそのまま渡し、
  feedparser自身の文字コード判定(XML宣言・HTTPヘッダ)に任せる。
- 見出し・要約のみを保存し、本文は取得しない(docs/CONCEPT.md の指示)。

ローカルで最初に確認すべき点
----------------------------
- 実際の NHK RSS の `<description>` の有無(付いていない配信もある。無ければ空文字で保存)。
- 他紙(全国紙など)の RSS URL を config に追加する際、Shift_JIS 配信でないか
  (feedparser が誤判定する場合は `response.encoding` を明示的に上書きする)。
- `pubDate` のタイムゾーン表記が `+0900` 以外(`GMT` など)になっていないか。
- 同じ記事が複数フィードに重複して出てくる場合の news_id(URL ハッシュ)の重複具合。
"""
from __future__ import annotations

import calendar as _calendar
from datetime import datetime, timezone
from typing import Any

import feedparser

from assoc.ingest.common import (
    RateLimiter,
    news_id_from_url,
    normalize_text,
    text_hash,
    upsert_news_items,
)
from assoc.ingest.models import NewsItem
from assoc.timeutil import utcnow


def _entry_published_at(entry: dict[str, Any]) -> datetime | None:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct:
        return None
    return datetime.fromtimestamp(_calendar.timegm(struct), tz=timezone.utc)


def parse_feed(
    raw: bytes | str,
    *,
    source_name: str,
    tier: int,
    first_observed_at: datetime | None = None,
    language: str = "ja",
) -> list[NewsItem]:
    """RSS の生データ(バイト列または文字列)を NewsItem のリストにする(純関数)。"""
    first_observed_at = first_observed_at or utcnow()
    parsed = feedparser.parse(raw)
    items: list[NewsItem] = []
    for entry in parsed.entries:
        url = (entry.get("link") or "").strip()
        title = normalize_text(entry.get("title") or "")
        if not url or not title:
            continue
        summary = normalize_text(entry.get("summary") or entry.get("description") or "")
        items.append(
            NewsItem(
                news_id=news_id_from_url(url),
                source=source_name,
                source_tier=tier,
                title=title,
                summary=summary,
                url=url,
                language=language,
                published_at=_entry_published_at(entry),
                first_observed_at=first_observed_at,
                text_hash=text_hash(title, summary),
            )
        )
    return items


def fetch_rss(cfg, con) -> int:
    """config の collect.rss_feeds をすべて取得し、news_item へ保存する。件数を返す。"""
    collect = cfg.section("collect")
    feeds = collect.get("rss_feeds", []) or []
    limiter = RateLimiter(
        float(collect.get("request_interval_sec", 1.0)),
        collect.get("user_agent", "assoc-research/0.1"),
    )
    total = 0
    for feed in feeds:
        name = feed.get("name", feed.get("url", "rss"))
        tier = int(feed.get("tier", 2))
        resp = limiter.get(feed["url"])
        items = parse_feed(resp.content, source_name=name, tier=tier)
        total += upsert_news_items(con, items)
    return total
