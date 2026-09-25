"""JPX の上場銘柄一覧の収集(docs/DESIGN.md §3・§11)。担当範囲は取得部分のみ。

想定している形式
----------------
- JPX が公開する「上場銘柄一覧」(通称 `data_j.xls`)。配布ページ:
  `https://www.jpx.co.jp/markets/statistics-equities/misc/01.html`
  ダウンロードリンクの実 URL は JPX 側の更新のたびに変わる(`.../tvdivq...-att/data_j.xls`
  のような、内容ハッシュを含む URL になっている)ため、**固定 URL を置かず**
  `cfg.section("collect").get("jpx_universe_url")` で設定から渡す前提にする
  (見本 `config/config.example.yaml` に追加することを提案。本ファイルでは変更していない)。
- 拡張子は `.xls`(旧 Excel 形式)だが、JPX 側で `.xlsx` に変わっている可能性もあるため、
  拡張子で `xlrd`(.xls)/`openpyxl`(.xlsx)を切り替える。
- 列名(記憶に基づく想定。要確認):
  `日付`, `コード`, `銘柄名`, `市場・商品区分`, `33業種コード`, `33業種区分`,
  `17業種コード`, `17業種区分`, `規模コード`, `規模区分`
- 監理・整理ポストの銘柄は `data_j.xls` には含まれない(JPX が別ページで公表)。
  **取得先の URL・形式が未確認のため、`fetch_monitoring_posts` は TODO とし、
  常に空リストを返す**(呼び出し側で `status` は「通常」のままになる)。

ローカルで最初に確認すべき点
----------------------------
- `data_j.xls` の実際の列名・シート名(複数シートの場合、既定は先頭シートを想定)。
- ダウンロード URL(配布ページを開いてリンク先を確認し、`config.yaml` の
  `collect.jpx_universe_url` に設定する)。
- 拡張子が `.xls` か `.xlsx` か(`requirements.txt` に `xlrd`(.xls用)と
  `openpyxl`(.xlsx用)の追加が必要になる可能性が高い。両方を提案する)。
- 監理・整理ポストの公表ページの URL と形式(見つかれば `fetch_monitoring_posts` を実装する)。
- 証券コードが4桁の英数字混在(新形式)になっていないか。
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from assoc.ingest.common import RateLimiter, log_fetch
from assoc.timeutil import jst_date, to_iso, utcnow

_COLUMN_MAP = {
    "コード": "code",
    "銘柄名": "company_name",
    "市場・商品区分": "market",
    "33業種区分": "industry33",
}


@dataclass
class UniverseRow:
    code: str
    company_name: str
    market: str
    industry33: str
    status: str = "通常"  # 通常 | 監理 | 整理


def _pick_engine(filename_or_url: str) -> str:
    return "openpyxl" if filename_or_url.lower().endswith(".xlsx") else "xlrd"


def parse_universe_excel(raw: bytes | str | Path, *, source_name: str = "data_j.xls") -> list[UniverseRow]:
    """JPX の上場銘柄一覧(Excel)を UniverseRow のリストにする(純関数)。

    raw はバイト列(ダウンロードした内容)、または(テスト用に)ローカルファイルのパス。
    """
    buf = io.BytesIO(raw) if isinstance(raw, (bytes, bytearray)) else raw
    engine = _pick_engine(str(source_name if isinstance(raw, (bytes, bytearray)) else raw))
    df = pd.read_excel(buf, engine=engine)
    df = df.rename(columns=_COLUMN_MAP)
    missing = [c for c in ("code", "company_name", "market", "industry33") if c not in df.columns]
    if missing:
        raise ValueError(f"上場銘柄一覧に想定した列がありません: {missing}(実際の列: {list(df.columns)})")
    rows = []
    for _, r in df.iterrows():
        code = str(r["code"]).strip()
        if not code or code.lower() == "nan":
            continue
        rows.append(
            UniverseRow(
                code=code,
                company_name=str(r["company_name"]).strip(),
                market=str(r["market"]).strip(),
                industry33=str(r.get("industry33", "")).strip(),
            )
        )
    return rows


def fetch_monitoring_posts(cfg) -> dict[str, str]:
    """監理・整理ポストの銘柄コード→区分('監理'|'整理')の辞書を返す。

    TODO: JPX の公表ページの URL・形式が未確認のため未実装。実装できるまでは常に空を返し、
    universe_daily の status はすべて「通常」になる(docs/DESIGN.md §8.1 の除外条件のうち
    監理・整理ポストの判定は、実装されるまで機能しない)。
    """
    return {}


def upsert_universe(con, rows: list[UniverseRow], as_of: date, first_observed_at=None) -> int:
    first_observed_at = first_observed_at or utcnow()
    n = 0
    for row in rows:
        con.execute(
            """INSERT INTO universe_daily (date, code, company_name, market, industry33, status, first_observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (date, code) DO UPDATE SET
                 company_name = excluded.company_name, market = excluded.market,
                 industry33 = excluded.industry33, status = excluded.status""",
            [as_of.isoformat(), row.code, row.company_name, row.market, row.industry33, row.status,
             to_iso(first_observed_at)],
        )
        n += 1
    return n


def fetch_universe(cfg, con, url: str | None = None, as_of: date | None = None) -> int:
    """JPX の上場銘柄一覧を取得し、universe_daily(当日分)へ保存する。

    url を渡さない場合は config の collect.jpx_universe_url を使う。設定が無ければ
    例外を投げる(呼び出し側の safe_run が fetch_log に記録する)。
    """
    as_of = as_of or jst_date(utcnow())
    collect = cfg.section("collect")
    url = url or collect.get("jpx_universe_url")
    if not url:
        log_fetch(con, "jpx_universe", True, 0, "collect.jpx_universe_url が未設定のためスキップ")
        return 0
    limiter = RateLimiter(
        float(collect.get("request_interval_sec", 1.0)), collect.get("user_agent", "assoc-research/0.1")
    )
    resp = limiter.get(url)
    rows = parse_universe_excel(resp.content, source_name=url)
    saved = upsert_universe(con, rows, as_of)
    posts = fetch_monitoring_posts(cfg)
    if posts:
        for code, status in posts.items():
            con.execute(
                "UPDATE universe_daily SET status = ? WHERE date = ? AND code = ?",
                [status, as_of.isoformat(), code],
            )
    return saved
