from __future__ import annotations

from datetime import date

from assoc.ingest.google_news import aggregate_media_count, parse_search_rss, tier_for_media
from ingest_helpers import fixture_bytes


def test_parse_search_rss_extracts_media_and_strips_suffix():
    items = parse_search_rss(fixture_bytes("google_news_sample.xml"), keyword="半導体")
    assert len(items) == 3
    titles = {i.title for i in items}
    assert "半導体大手が増産へ" in titles
    assert all(not t.endswith("- 日本経済新聞") for t in titles)


def test_tier_for_media_known_vs_unknown():
    assert tier_for_media("日本経済新聞") == 2
    assert tier_for_media("ロイター") == 2
    assert tier_for_media("株式新聞") == 3


def test_aggregate_media_count_counts_distinct_media_and_articles():
    points = aggregate_media_count(
        fixture_bytes("google_news_sample.xml"), keyword="半導体", target_date=date(2026, 9, 25)
    )
    by_source = {p.source: p.value for p in points}
    assert by_source["google_news_media"] == 3  # 日経・株式新聞・ロイターの3媒体
    assert by_source["google_news_articles"] == 3
