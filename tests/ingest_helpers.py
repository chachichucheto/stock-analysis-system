"""ingest/events のテストで共有する小さなヘルパー。

conftest.py にしていないのは、他の担当領域(scoring/tracking/commit など)のテストと
ファイルが競合しないようにするため。各テストから明示的に import して使う。
"""
from __future__ import annotations

from pathlib import Path

from assoc.config import Config, Thresholds
from assoc.store.db import connect

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def make_config(**collect_overrides) -> Config:
    collect = {
        "rss_feeds": [{"name": "NHK 主要ニュース", "url": "https://example.invalid/rss", "tier": 2}],
        "google_news_queries": ["半導体"],
        "request_interval_sec": 0.0,  # テストでは待たない
        "user_agent": "assoc-test/0.1",
    }
    collect.update(collect_overrides)
    return Config(raw={"paths": {"data_dir": "data"}, "collect": collect, "edinet": {"api_key": ""}},
                  thresholds=Thresholds())


def make_db(tmp_path: Path):
    return connect(tmp_path / "data")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")
