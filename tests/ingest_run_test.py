from __future__ import annotations

from datetime import timedelta

from assoc.ingest.run import collect, consecutive_failures, sources_with_consecutive_failures
from assoc.timeutil import to_iso, utcnow
from ingest_helpers import make_config, make_db

_BASE = utcnow()


def _ts(offset_sec: int) -> str:
    # to_iso は秒単位に丸めるため、同一秒への丸めで順序があいまいにならないよう
    # テストでは意図的に1秒以上ずらしたタイムスタンプを使う。
    return to_iso(_BASE + timedelta(seconds=offset_sec))


def test_collect_does_not_raise_on_unreachable_source(tmp_path):
    """収集先に接続できなくても例外を出さず、fetch_log に記録して次に進む(DESIGN §6.1)。"""
    cfg = make_config()
    con = make_db(tmp_path)
    results = collect(cfg, con, sources=["rss"])
    assert "rss" in results
    assert results["rss"].ok is False  # example.invalid には接続できない
    logged = con.execute("SELECT ok FROM fetch_log WHERE source = 'rss'").fetchall()
    assert len(logged) == 1
    assert logged[0][0] is False


def test_collect_skips_sources_not_requested(tmp_path):
    cfg = make_config()
    con = make_db(tmp_path)
    results = collect(cfg, con, sources=["edinet"])  # api_key 未設定でスキップされる
    assert set(results) == {"edinet"}
    assert results["edinet"].ok is True
    assert results["edinet"].items == 0


def test_consecutive_failures_detects_three_in_a_row(tmp_path):
    con = make_db(tmp_path)
    for i, ok in enumerate((True, False, False, False)):
        con.execute(
            "INSERT INTO fetch_log (source, fetched_at, ok, items, message) VALUES (?, ?, ?, 0, '')",
            ["rss", _ts(i), ok],
        )
    assert consecutive_failures(con, "rss", n=3) is True
    assert consecutive_failures(con, "google_news", n=3) is False  # 記録が無い


def test_consecutive_failures_false_when_recent_success_mixed_in(tmp_path):
    con = make_db(tmp_path)
    for i, ok in enumerate((False, True, False)):
        con.execute(
            "INSERT INTO fetch_log (source, fetched_at, ok, items, message) VALUES (?, ?, ?, 0, '')",
            ["tdnet", _ts(i), ok],
        )
    assert consecutive_failures(con, "tdnet", n=3) is False


def test_sources_with_consecutive_failures_lists_only_failing_ones(tmp_path):
    con = make_db(tmp_path)
    for src, oks in {"rss": [False, False, False], "tdnet": [True, True, True]}.items():
        for i, ok in enumerate(oks):
            con.execute(
                "INSERT INTO fetch_log (source, fetched_at, ok, items, message) VALUES (?, ?, ?, 0, '')",
                [src, _ts(i), ok],
            )
    failing = sources_with_consecutive_failures(con, sources=["rss", "tdnet"])
    assert failing == ["rss"]
