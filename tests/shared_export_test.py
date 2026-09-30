from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta

import duckdb

from assoc.shared_export import export_shared
from assoc.store.db import connect
from assoc.timeutil import JST

TODAY = date(2026, 9, 30)


def _at(d: date, hour: int = 12) -> datetime:
    return datetime.combine(d, time(hour, 0), tzinfo=JST)


def _seed(con):
    for i, d in enumerate([TODAY - timedelta(days=3), TODAY - timedelta(days=1), TODAY]):
        con.execute("INSERT INTO news_item VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [f"n{i}", "NHK 経済", 2, f"見出し{i}", "", f"https://example.com/{i}", "ja", _at(d), _at(d), f"h{i}"])
        con.execute("INSERT INTO disclosure VALUES (?,?,?,?,?,?,?,?,?)",
                    [f"d{i}", "tdnet", "1301", "サンプル物産", f"開示{i}", "", f"https://example.com/d{i}", _at(d), _at(d)])
    con.execute("INSERT INTO news_item VALUES ('g','google_news:半導体',3,'記事','日本経済新聞','https://example.com/g','ja',?,?,'hg')",
                [_at(TODAY), _at(TODAY)])
    con.execute("INSERT INTO universe_daily VALUES (?,?,?,?,?,?,?)",
                [TODAY, "1301", "サンプル物産", "プライム", "水産・農林業", "通常", _at(TODAY)])
    con.execute("INSERT INTO calendar (code, earnings_date, earnings_estimated) VALUES ('1301', ?, FALSE)", [date(2026, 11, 10)])


def test_export_writes_days_media_and_manifest(tmp_path):
    con = connect(tmp_path / "data")
    _seed(con)
    shared = tmp_path / "shared"
    r = export_shared(con, shared, TODAY)
    assert r["written"] > 0
    files = {p.relative_to(shared).as_posix() for p in shared.rglob("*.parquet")}
    assert "news/news_20260930.parquet" in files and "disclosure/disclosure_20260930.parquet" in files
    assert "universe.parquet" in files and "calendar.parquet" in files

    rows = duckdb.connect().execute(
        f"SELECT source, media FROM read_parquet('{(shared / 'news' / 'news_20260930.parquet').as_posix()}') ORDER BY source").fetchall()
    assert ("NHK 経済", "NHK") in rows                       # フィード名から媒体名(NHK)を作る
    assert ("google_news:半導体", "日本経済新聞") in rows      # Google News は実際の媒体名

    m = json.loads((shared / "manifest.json").read_text(encoding="utf-8"))
    assert m["schema_version"] == 1 and m["latest"]["news_day"] == "20260930"
    assert m["files"]["news/news_20260930.parquet"]["rows"] == 2
    assert "generated_at" in m and "rules" in m


def test_old_days_are_not_rewritten(tmp_path):
    con = connect(tmp_path / "data")
    _seed(con)
    shared = tmp_path / "shared"
    export_shared(con, shared, TODAY)
    old = shared / "news" / "news_20260927.parquet"           # 3日前(書き直し対象より前)
    yesterday = shared / "news" / "news_20260929.parquet"
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(yesterday, (1_000_000_000, 1_000_000_000))
    r = export_shared(con, shared, TODAY)
    assert r["skipped"] >= 2                                  # 3日前のニュースと開示は変更しない
    assert old.stat().st_mtime == 1_000_000_000               # 触っていない
    assert yesterday.stat().st_mtime > 1_000_000_000          # 昨日は書き直す(夜遅い取得分を拾うため)


def test_empty_db_exports_only_manifest(tmp_path):
    con = connect(tmp_path / "data")
    r = export_shared(con, tmp_path / "shared", TODAY)
    assert r["written"] == 0
    assert (tmp_path / "shared" / "manifest.json").exists()
