"""収集器をまとめて実行する(docs/DESIGN.md §6.1)。

`collect()` は各収集器を1つずつ `safe_run` 経由で呼び、失敗しても次の収集器に進む。
結果は `fetch_log` に記録され、この関数はその要約(収集器ごとの成否・件数)を返す。
"""
from __future__ import annotations

from datetime import date

from assoc.ingest import edinet, gdelt, google_news, rss, tdnet, wikipedia
from assoc.ingest.common import FetchResult, safe_run
from assoc.master import calendar as master_calendar
from assoc.master import universe as master_universe

ALL_SOURCES = ("rss", "google_news", "tdnet", "edinet", "gdelt", "wikipedia", "universe", "calendar")


def collect(
    cfg,
    con,
    sources: list[str] | None = None,
    *,
    google_news_keywords: list[str] | None = None,
    gdelt_keywords: list[str] | None = None,
    wiki_articles: list[wikipedia.WikiArticle] | None = None,
    tdnet_day: date | None = None,
) -> dict[str, FetchResult]:
    """収集器を順に実行する。`sources` を指定すれば、その一部だけを実行する。

    `con` は `assoc.store.db.connect()` で開いた DuckDB の接続を渡す(このモジュールでは開かない。
    DB の生成・スキーマは既存の store/db.py の管轄のため)。
    """
    targets = sources if sources is not None else list(ALL_SOURCES)
    results: dict[str, FetchResult] = {}

    if "rss" in targets:
        results["rss"] = safe_run(con, "rss", lambda: rss.fetch_rss(cfg, con))
    if "google_news" in targets:
        results["google_news"] = safe_run(
            con, "google_news", lambda: google_news.fetch_google_news(cfg, con, google_news_keywords)
        )
    if "tdnet" in targets:
        results["tdnet"] = safe_run(con, "tdnet", lambda: tdnet.fetch_tdnet(cfg, con, tdnet_day, tdnet_day))
    if "edinet" in targets:
        results["edinet"] = safe_run(con, "edinet", lambda: edinet.fetch_edinet(cfg, con, tdnet_day))
    if "gdelt" in targets:
        results["gdelt"] = safe_run(con, "gdelt", lambda: gdelt.fetch_gdelt(cfg, con, gdelt_keywords))
    if "wikipedia" in targets:
        # 記事名は引数か、config の collect.wikipedia_articles(日本語版の記事名)から
        articles = wiki_articles or [wikipedia.WikiArticle(title=t) for t in
                                     (cfg.section("collect").get("wikipedia_articles") or [])]
        results["wikipedia"] = safe_run(con, "wikipedia", lambda: wikipedia.fetch_wikipedia(cfg, con, articles))
    if "universe" in targets:
        results["universe"] = safe_run(con, "jpx_universe", lambda: master_universe.fetch_universe(cfg, con))
    if "calendar" in targets:
        results["calendar"] = safe_run(
            con, "earnings_schedule", lambda: master_calendar.fetch_earnings_schedule(cfg, con)
        )
    return results


def consecutive_failures(con, source: str, n: int = 3) -> bool:
    """直近 n 回の fetch_log がすべて失敗(ok=False)なら True(docs/DESIGN.md §6.1)。

    記録が n 回に満たない場合は False とする(判定に十分な材料がないため)。
    """
    # fetched_at は秒単位に丸めているため、同一秒の記録が複数あっても順序が安定するよう
    # rowid(挿入順)を副次キーにする。
    rows = con.execute(
        "SELECT ok FROM fetch_log WHERE source = ? ORDER BY fetched_at DESC, rowid DESC LIMIT ?",
        [source, n],
    ).fetchall()
    if len(rows) < n:
        return False
    return all(not r[0] for r in rows)


def sources_with_consecutive_failures(con, sources: list[str] | None = None, n: int = 3) -> list[str]:
    """3回続けて失敗している収集器の一覧(レポート冒頭の警告に使う)。"""
    # fetch_log に記録される名前は、universe と calendar だけ収集器の名前と違う
    logged = {"universe": "jpx_universe", "calendar": "earnings_schedule"}
    targets = sources if sources is not None else list(ALL_SOURCES)
    return [s for s in targets if consecutive_failures(con, logged.get(s, s), n)]
