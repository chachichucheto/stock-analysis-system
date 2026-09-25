"""収集器で共通に使う道具。

- HTTP 取得は間隔を空け、user_agent を付ける(相手先に負荷をかけないため。docs/CONCEPT.md の指示)。
- 失敗は例外で落とさず fetch_log に記録して次の収集器に進む(docs/DESIGN.md §6.1)。
- news_id・text_hash・novelty_hash の元になる正規化とハッシュをここにまとめる。

このモジュール自体は外部接続を行わない(テスト対象)。実際の HTTP 取得は RateLimiter.get 経由で
各収集器から呼ぶ。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable

import requests

from assoc.timeutil import to_iso, utcnow


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """見出し・要約の正規化。連続する空白を1つにし、前後を削る。

    全角/半角の統一は行わない(誤爆を避けるため)。novelty_hash 用の強い正規化は
    events/cluster.py 側で別途行う。
    """
    return " ".join((text or "").split())


def news_id_from_url(url: str) -> str:
    """URL のハッシュを news_id とする(見出し語で見出しの表記ゆれを吸収しないため、
    同じ記事が複数回取れても URL が同じなら同じ news_id になる)。"""
    return sha256_hex(url.strip())


def text_hash(title: str, summary: str) -> str:
    """見出し+要約の正規化ハッシュ。表記ゆれのある重複記事の検出に使う。"""
    return sha256_hex(normalize_text(title) + "\n" + normalize_text(summary))


class RateLimiter:
    """collect.request_interval_sec の間隔を空けて HTTP を取得する薄いラッパー。"""

    def __init__(self, interval_sec: float, user_agent: str):
        self.interval_sec = max(0.0, interval_sec)
        self.headers = {"User-Agent": user_agent}
        self._last_call: float | None = None

    def get(self, url: str, timeout: float = 20.0, **kwargs) -> requests.Response:
        if self._last_call is not None:
            wait = self.interval_sec - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        try:
            resp = requests.get(url, headers=self.headers, timeout=timeout, **kwargs)
        finally:
            self._last_call = time.monotonic()
        resp.raise_for_status()
        return resp


@dataclass
class FetchResult:
    ok: bool
    items: int
    message: str = ""


def log_fetch(con, source: str, ok: bool, items: int, message: str = "") -> None:
    """fetch_log に1行記録する。"""
    con.execute(
        "INSERT INTO fetch_log (source, fetched_at, ok, items, message) VALUES (?, ?, ?, ?, ?)",
        [source, to_iso(utcnow()), ok, items, message[:500]],
    )


def safe_run(con, source: str, fn: Callable[[], int]) -> FetchResult:
    """収集器を1つ実行し、例外を握りつぶして fetch_log に記録する(docs/DESIGN.md §6.1)。

    3回続けての失敗の検出は ingest/run.py 側で fetch_log を集計して行う。
    """
    try:
        items = fn()
        log_fetch(con, source, True, items)
        return FetchResult(True, items)
    except Exception as e:  # noqa: BLE001 - どの収集器の失敗も全体を止めない
        log_fetch(con, source, False, 0, f"{type(e).__name__}: {e}")
        return FetchResult(False, 0, str(e))


def upsert_news_items(con, items: list) -> int:
    """news_item へ INSERT OR IGNORE する(重複 URL は無視)。"""
    n = 0
    for it in items:
        before = con.execute("SELECT count(*) FROM news_item WHERE news_id = ?", [it.news_id]).fetchone()[0]
        con.execute(
            """INSERT OR IGNORE INTO news_item
               (news_id, source, source_tier, title, summary, url, language,
                published_at, first_observed_at, text_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [it.news_id, it.source, it.source_tier, it.title, it.summary, it.url, it.language,
             to_iso(it.published_at) if it.published_at else None,
             to_iso(it.first_observed_at), it.text_hash],
        )
        if before == 0:
            n += 1
    return n


def upsert_disclosures(con, items: list) -> int:
    """disclosure へ INSERT OR IGNORE する(重複 URL は無視)。"""
    n = 0
    for it in items:
        before = con.execute(
            "SELECT count(*) FROM disclosure WHERE disclosure_id = ?", [it.disclosure_id]
        ).fetchone()[0]
        con.execute(
            """INSERT OR IGNORE INTO disclosure
               (disclosure_id, source, code, company_name, title, doc_type, url,
                published_at, first_observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [it.disclosure_id, it.source, it.code, it.company_name, it.title, it.doc_type, it.url,
             to_iso(it.published_at) if it.published_at else None, to_iso(it.first_observed_at)],
        )
        if before == 0:
            n += 1
    return n


def upsert_attention(con, points: list) -> int:
    """attention_daily へ書く。同じ (key, source, date) は値を上書きする
    (1日に複数回取得して更新するため。first_observed_at は最初の取得時刻を保つ)。"""
    n = 0
    for p in points:
        con.execute(
            """INSERT INTO attention_daily (key, source, date, value, first_observed_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (key, source, date) DO UPDATE SET value = excluded.value""",
            [p.key, p.source, p.date, p.value, to_iso(p.first_observed_at)],
        )
        n += 1
    return n
