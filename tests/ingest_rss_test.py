from __future__ import annotations

from assoc.ingest.rss import parse_feed
from tests.ingest_helpers import fixture_bytes


def test_parse_feed_extracts_items():
    items = parse_feed(fixture_bytes("rss_sample.xml"), source_name="NHK 主要ニュース", tier=2)
    assert len(items) == 3
    first = items[0]
    assert first.title == "大手半導体メーカーが国内工場に新規投資を発表"
    assert first.url.startswith("https://www3.nhk.or.jp/")
    assert first.source_tier == 2
    assert first.source == "NHK 主要ニュース"
    assert first.published_at is not None
    assert first.summary  # 要約あり


def test_parse_feed_handles_missing_summary():
    items = parse_feed(fixture_bytes("rss_sample.xml"), source_name="NHK", tier=2)
    no_summary = [i for i in items if i.title == "見出しだけの記事(要約なし)"]
    assert len(no_summary) == 1
    assert no_summary[0].summary == ""


def test_news_id_is_stable_hash_of_url():
    items1 = parse_feed(fixture_bytes("rss_sample.xml"), source_name="NHK", tier=2)
    items2 = parse_feed(fixture_bytes("rss_sample.xml"), source_name="NHK", tier=2)
    assert items1[0].news_id == items2[0].news_id
    assert len(items1[0].news_id) == 64  # sha256 hex
