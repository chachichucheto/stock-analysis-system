from __future__ import annotations

from assoc.events.cluster import media_of


def test_same_media_feeds_count_once():
    # NHK の4本のフィードは、1つの媒体
    names = {media_of(s) for s in ["NHK 主要ニュース", "NHK 経済", "NHK 国際", "NHK 政治"]}
    assert names == {"NHK"}
    assert media_of("Yahoo!ニュース 国内") == media_of("Yahoo!ニュース IT") == "Yahoo!ニュース"


def test_google_news_uses_publisher_not_query():
    # 検索語(source)ではなく、要約欄に入れた実際の媒体名で数える
    assert media_of("google_news:半導体", "日本経済新聞") == "日本経済新聞"
    assert media_of("google_news:防衛", "日本経済新聞") == "日本経済新聞"    # 同じ媒体は1つ
    assert media_of("google_news:防衛", "") == "google_news"


def test_disclosure_sources_unchanged():
    assert media_of("tdnet") == "tdnet"
    assert media_of("") == ""
