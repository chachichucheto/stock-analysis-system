from __future__ import annotations

import json

from assoc.ingest.wikipedia import WikiArticle, parse_pageviews_json
from ingest_helpers import fixture_text


def test_parse_pageviews_json_extracts_daily_views():
    data = json.loads(fixture_text("wikipedia_pageviews_sample.json"))
    article = WikiArticle(title="半導体", project="ja")
    points = parse_pageviews_json(data, article=article)
    assert len(points) == 3
    by_date = {p.date: p.value for p in points}
    assert by_date["2026-09-25"] == 12000.0
    assert all(p.source == "wiki_ja" for p in points)
    assert all(p.key == "半導体" for p in points)


def test_parse_pageviews_json_uses_english_source_name():
    data = json.loads(fixture_text("wikipedia_pageviews_sample.json"))
    article = WikiArticle(title="Semiconductor", project="en")
    points = parse_pageviews_json(data, article=article)
    assert points[0].source == "wiki_en"
