from __future__ import annotations

import pytest

from assoc.config import Config
from assoc.ingest import google_news
from assoc.store.db import connect

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title>新工場を発表 - 日本経済新聞</title><link>https://example.com/a</link>
<pubDate>Wed, 30 Sep 2026 09:00:00 +0900</pubDate><source url="https://n.example">日本経済新聞</source></item>
</channel></rss>""".encode("utf-8")


class _Resp:
    def __init__(self, content: bytes):
        self.content = content


def _limiter_factory(fail_keywords: set[str]):
    class Limiter:
        def __init__(self, *a, **k):
            pass

        def get(self, url, **k):
            from urllib.parse import unquote
            kw = unquote(url.split("q=")[1].split("&")[0])
            if kw in fail_keywords:
                raise ConnectionError(f"404 Client Error: Not Found for url: {url}")
            return _Resp(RSS)
    return Limiter


def test_one_failing_keyword_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(google_news, "RateLimiter", _limiter_factory({"半導体"}))
    con = connect(tmp_path)
    cfg = Config(raw={"collect": {"google_news_queries": ["半導体", "防衛", "造船"]}})
    n = google_news.fetch_google_news(cfg, con)
    assert n >= 1                                            # 残りのキーワードは取得できている
    failed = con.execute("SELECT source, ok FROM fetch_log WHERE source LIKE 'google_news:%'").fetchall()
    assert ("google_news:半導体", False) in failed          # 失敗したキーワードは記録される


def test_all_keywords_failing_is_reported_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(google_news, "RateLimiter", _limiter_factory({"半導体", "防衛"}))
    con = connect(tmp_path)
    cfg = Config(raw={"collect": {"google_news_queries": ["半導体", "防衛"]}})
    with pytest.raises(RuntimeError):
        google_news.fetch_google_news(cfg, con)
