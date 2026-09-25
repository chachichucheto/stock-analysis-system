"""Google News RSS 検索による報道の広がりの計測(docs/DESIGN.md §3)。

想定している形式
----------------
- 検索 URL:`https://news.google.com/rss/search?q={quote(keyword)}&hl=ja&gl=JP&ceid=JP:ja`
  RSS 2.0 で返る。1記事が1 `<item>`。
- `<item><title>見出し - 媒体名</title><link>https://news.google.com/rss/articles/...</link>
  <pubDate>…</pubDate><source url="https://example.com">媒体名</source></item>`
  のように、`<title>` の末尾に " - 媒体名" が付き、`<source>` 要素にも媒体名が入る
  (feedparser では `entry.source.title` として取れる想定)。
- `<link>` は Google のリダイレクト URL であり、元記事の URL ではない
  (Google News のクリック計測ドメインを経由する)。

ローカルで最初に確認すべき点
----------------------------
- `entry.source` が feedparser のバージョンによって取れない場合、`<title>` 末尾の
  " - 媒体名" から抽出するフォールバックで代替できているか。
- クエリに日本語キーワードを含めたときの URL エンコード・文字化けの有無。
- 同じ記事が複数キーワードにヒットしたときに、news_item 側で URL 重複が正しく無視されるか
  (Google のリダイレクト URL がキーワードごとに変わらないか)。
- 1日に何度も叩くとレート制限(429)が返らないか。
"""
from __future__ import annotations

import calendar as _calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import quote

import feedparser

from assoc.ingest.common import (
    RateLimiter,
    news_id_from_url,
    normalize_text,
    text_hash,
    upsert_attention,
    upsert_news_items,
)
from assoc.ingest.models import AttentionPoint, NewsItem
from assoc.timeutil import jst_date, utcnow

SEARCH_URL = "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"

# 大手報道機関は tier 2、それ以外は判定できないので tier 3 とする(docs/CONCEPT.md §7.3)。
_TIER2_MEDIA = {
    "NHK", "日本経済新聞", "日経クロステック", "朝日新聞", "読売新聞", "毎日新聞",
    "産経新聞", "共同通信", "時事通信", "ロイター", "Reuters", "ブルームバーグ",
    "Bloomberg", "東洋経済オンライン", "ダイヤモンド・オンライン",
}


def tier_for_media(media_name: str) -> int:
    return 2 if media_name in _TIER2_MEDIA else 3


@dataclass
class _Entry:
    url: str
    title: str
    media: str
    published_at: datetime | None


def _entry_media(entry: dict) -> str:
    source = entry.get("source")
    if isinstance(source, dict) and source.get("title"):
        return source["title"].strip()
    # フォールバック:タイトル末尾の " - 媒体名"
    m = re.search(r"\s-\s([^-]+)$", entry.get("title") or "")
    return m.group(1).strip() if m else ""


def _entry_title(entry: dict, media_name: str) -> str:
    title = entry.get("title") or ""
    if media_name and title.endswith(f" - {media_name}"):
        title = title[: -(len(media_name) + 3)]
    return normalize_text(title)


def _entry_published_at(entry: dict) -> datetime | None:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct:
        return None
    return datetime.fromtimestamp(_calendar.timegm(struct), tz=timezone.utc)


def _parse_entries(raw: bytes | str) -> list[_Entry]:
    parsed = feedparser.parse(raw)
    out = []
    for entry in parsed.entries:
        url = (entry.get("link") or "").strip()
        media = _entry_media(entry)
        title = _entry_title(entry, media)
        if not url or not title:
            continue
        out.append(_Entry(url=url, title=title, media=media, published_at=_entry_published_at(entry)))
    return out


def parse_search_rss(
    raw: bytes | str, *, keyword: str, first_observed_at: datetime | None = None
) -> list[NewsItem]:
    """検索結果 RSS を NewsItem のリストにする(純関数)。"""
    first_observed_at = first_observed_at or utcnow()
    items = []
    for e in _parse_entries(raw):
        items.append(
            NewsItem(
                news_id=news_id_from_url(e.url),
                source=f"google_news:{keyword}",
                source_tier=tier_for_media(e.media),
                title=e.title,
                summary=e.media,  # 見出しに含まれない媒体名を要約欄に残す(本文は保存しない)
                url=e.url,
                language="ja",
                published_at=e.published_at,
                first_observed_at=first_observed_at,
                text_hash=text_hash(e.title, e.media),
            )
        )
    return items


def aggregate_media_count(
    raw: bytes | str, *, keyword: str, target_date: date, first_observed_at: datetime | None = None
) -> list[AttentionPoint]:
    """target_date(日本時間)に公開された記事から、記事数と媒体数を数える(純関数)。

    published_at が取れない記事(Google News では稀にある)は target_date のものとみなす
    (取得日にほぼ等しいため)。
    """
    first_observed_at = first_observed_at or utcnow()
    entries = [
        e for e in _parse_entries(raw)
        if e.published_at is None or jst_date(e.published_at) == target_date
    ]
    media_count = len({e.media for e in entries if e.media})
    date_str = target_date.isoformat()
    return [
        AttentionPoint(
            key=keyword, source="google_news_media", date=date_str,
            value=float(media_count), first_observed_at=first_observed_at,
        ),
        AttentionPoint(
            key=keyword, source="google_news_articles", date=date_str,
            value=float(len(entries)), first_observed_at=first_observed_at,
        ),
    ]


def fetch_google_news(cfg, con, extra_keywords: list[str] | None = None) -> int:
    """config の collect.google_news_queries(+ extra_keywords)を検索し、
    news_item と attention_daily(source='google_news_media' / 'google_news_articles')へ保存する。
    保存した news_item の件数を返す。
    """
    collect = cfg.section("collect")
    keywords = list(dict.fromkeys((collect.get("google_news_queries") or []) + (extra_keywords or [])))
    limiter = RateLimiter(
        float(collect.get("request_interval_sec", 1.0)),
        collect.get("user_agent", "assoc-research/0.1"),
    )
    total = 0
    today = jst_date(utcnow())
    for kw in keywords:
        resp = limiter.get(SEARCH_URL.format(q=quote(kw)))
        total += upsert_news_items(con, parse_search_rss(resp.content, keyword=kw))
        upsert_attention(con, aggregate_media_count(resp.content, keyword=kw, target_date=today))
    return total
