"""共有棚(data/shared/)の書き出し。他のシステム(既存のファンダメンタルズ・CIS など)が、連想モデルの収集結果を
**読み取り専用**で使えるようにする(docs/PROPOSAL_HYBRID_AND_SHARING.md §2.2.1・§2.3)。

方針
- ニュースの機械収集は連想側が大元。他のシステムは、このフォルダのファイルを読むだけにする。
- DuckDB のデータベース本体は共有しない(書き込み中は他のプロセスが読めず、日次処理と衝突するため)。
  代わりに Parquet(列指向・型つき)のファイルを日ごとに書き出す。
- **過去の日のファイルは書き換えない**(今日と昨日だけ書き直す。夜遅くに取得した分を、翌日の書き出しで拾うため)。
- 書き出しは一時ファイルに書いてから置き換える(読んでいる側が、書きかけのファイルを見ないように)。
- `manifest.json` に、作成時刻・各ファイルの行数と期間・列の定義を書く。読む側は、これで鮮度を確認する。

ファイル
- `news/news_YYYYMMDD.parquet`:news_id, published_at(UTC), first_observed_at(UTC), source(フィード名), media(媒体名),
  tier(1公的〜4個人), title, url, summary, language
- `disclosure/disclosure_YYYYMMDD.parquet`:disclosure_id, source(tdnet|edinet), code(4桁), company_name, title,
  doc_type, url, published_at(UTC), first_observed_at(UTC)
- `universe.parquet`:最新日の上場銘柄一覧(code, company_name, market, industry33, status[通常|監理|整理], date)
- `calendar.parquet`:決算発表予定日(code, earnings_date, earnings_estimated)
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from assoc.events.cluster import media_of
from assoc.timeutil import JST, to_iso, utcnow

SCHEMA_VERSION = 1
NEWS_COLUMNS = ["news_id", "published_at", "first_observed_at", "source", "media", "tier", "title", "url",
                "summary", "language"]
DISCLOSURE_COLUMNS = ["disclosure_id", "source", "code", "company_name", "title", "doc_type", "url",
                      "published_at", "first_observed_at"]


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    con = duckdb.connect()
    try:
        con.register("df", df)
        con.execute(f"COPY df TO '{tmp.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    tmp.replace(path)


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=JST)
    return start, start + timedelta(days=1)


def _news_frame(con, d: date) -> pd.DataFrame:
    s, e = _day_bounds(d)
    df = con.execute(
        """SELECT news_id, published_at, first_observed_at, source, source_tier AS tier, title, url, summary, language
           FROM news_item WHERE first_observed_at >= ? AND first_observed_at < ? ORDER BY first_observed_at, news_id""",
        [s, e]).fetchdf()
    df["media"] = [media_of(str(a or ""), str(b or "")) for a, b in zip(df["source"], df["summary"])]
    return df[NEWS_COLUMNS]


def _disclosure_frame(con, d: date) -> pd.DataFrame:
    s, e = _day_bounds(d)
    return con.execute(
        """SELECT disclosure_id, source, code, company_name, title, doc_type, url, published_at, first_observed_at
           FROM disclosure WHERE first_observed_at >= ? AND first_observed_at < ? ORDER BY first_observed_at, disclosure_id""",
        [s, e]).fetchdf()[DISCLOSURE_COLUMNS]


def _days_with_data(con, table: str) -> list[date]:
    row = con.execute(f"SELECT min(first_observed_at), max(first_observed_at) FROM {table}").fetchone()
    if row[0] is None:
        return []
    first = row[0].astimezone(JST).date()
    last = row[1].astimezone(JST).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def export_shared(con, shared_dir: Path, today: date) -> dict:
    """共有棚を更新し、manifest.json を書く。書き出したファイルの数などを返す。"""
    shared_dir = Path(shared_dir)
    shared_dir.mkdir(parents=True, exist_ok=True)
    written, skipped = 0, 0
    refresh_from = today - timedelta(days=1)            # 今日と昨日だけ書き直す。それより前の日は書き換えない

    for table, sub, prefix, frame in (("news_item", "news", "news", _news_frame),
                                      ("disclosure", "disclosure", "disclosure", _disclosure_frame)):
        for d in _days_with_data(con, table):
            path = shared_dir / sub / f"{prefix}_{d.strftime('%Y%m%d')}.parquet"
            if path.exists() and d < refresh_from:
                skipped += 1
                continue
            df = frame(con, d)
            if df.empty and not path.exists():
                continue
            _write_parquet(df, path)
            written += 1

    uni = con.execute(
        """SELECT code, company_name, market, industry33, status, date FROM universe_daily
           WHERE date = (SELECT max(date) FROM universe_daily) ORDER BY code""").fetchdf()
    if not uni.empty:
        _write_parquet(uni, shared_dir / "universe.parquet")
    cal = con.execute("SELECT code, earnings_date, earnings_estimated FROM calendar ORDER BY code").fetchdf()
    if not cal.empty:
        _write_parquet(cal, shared_dir / "calendar.parquet")

    manifest = _manifest(shared_dir)
    tmp = shared_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(shared_dir / "manifest.json")
    return {"written": written, "skipped": skipped, "files": len(manifest["files"])}


def _manifest(shared_dir: Path) -> dict:
    con = duckdb.connect()
    files: dict[str, dict] = {}
    try:
        for path in sorted(shared_dir.rglob("*.parquet")):
            if path.name.endswith(".tmp.parquet"):
                continue
            rel = path.relative_to(shared_dir).as_posix()
            n = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
            files[rel] = {"rows": n, "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")}
    finally:
        con.close()
    news_days = sorted(k[-16:-8] for k in files if k.startswith("news/"))
    disc_days = sorted(k[-16:-8] for k in files if k.startswith("disclosure/"))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": to_iso(utcnow()),
        "owner": "ニュース連想モデル(クロード連想モデル)。ニュース・開示の機械収集の大元。読み取り専用で使うこと",
        "rules": ["過去の日のファイルは書き換えない(今日と昨日だけ書き直す)",
                  "時刻は UTC(first_observed_at は連想側が取得した時刻)。日付の区切りは日本時間",
                  "読む前に generated_at を確認し、古ければ警告する",
                  "初回のバックフィル(2026-09-30)で取り込んだ過去分は first_observed_at が取り込み日になっている。"
                  "本来の公表時刻は published_at を使うこと"],
        "latest": {"news_day": news_days[-1] if news_days else None,
                   "disclosure_day": disc_days[-1] if disc_days else None},
        "columns": {"news": NEWS_COLUMNS, "disclosure": DISCLOSURE_COLUMNS,
                    "universe": ["code", "company_name", "market", "industry33", "status", "date"],
                    "calendar": ["code", "earnings_date", "earnings_estimated"]},
        "files": files,
    }
