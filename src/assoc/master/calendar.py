"""決算発表予定日の取得(docs/DESIGN.md §3)。担当範囲は取得部分のみ
(権利落ち日の計算、信用規制の取得は対象外。calendar テーブルの他の列は触らない)。

想定している形式
----------------
- 東証は四半期ごとに、上場会社の決算発表予定日をまとめた一覧(Excel または CSV)を
  公表している(通称「決算発表予定日」一覧)。配布ページの URL・ファイル形式は
  未確認のため、`cfg.section("collect").get("earnings_schedule_url")` で設定から
  渡す前提にする(見本 `config/config.example.yaml` への追加を提案。本ファイルでは
  変更していない)。
- 列名(記憶に基づく想定。要確認):`コード`, `会社名`, `決算期`, `発表予定日`。
  `発表予定日` は `YYYY/MM/DD` 形式を想定する。
- **取得できない銘柄・未公表の期間は前年同期の実績日から推定する**
  (docs/CONCEPT.md §3・docs/DESIGN.md §3 の指示どおり、推定は `earnings_estimated=True` を
  必ず付けて区別する)。前年実績からの推定は、`calendar` テーブルに前年の `earnings_date` が
  無いと行えないため、本モジュールだけでは完結しない(過去の実績を積み上げてから使える)。

ローカルで最初に確認すべき点
----------------------------
- 実際の配布 URL・ファイル形式(Excel か CSV か、列名)。
- 発表予定日が「未定」の会社の表記(空欄か、"未定" という文字列か)。
- 東証以外の市場(名証・福証・札証)の重複コードの扱い。
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from assoc.ingest.common import RateLimiter, log_fetch
from assoc.timeutil import to_iso, utcnow

_COLUMN_MAP = {"コード": "code", "会社名": "company_name", "発表予定日": "earnings_date"}


@dataclass
class EarningsScheduleRow:
    code: str
    earnings_date: date | None
    estimated: bool = False


def parse_earnings_schedule(raw: bytes, *, filename: str = "schedule.xlsx") -> list[EarningsScheduleRow]:
    """決算発表予定日一覧(Excel)を EarningsScheduleRow のリストにする(純関数)。"""
    engine = "openpyxl" if filename.lower().endswith(".xlsx") else "xlrd"
    df = pd.read_excel(io.BytesIO(raw), engine=engine)
    df = df.rename(columns=_COLUMN_MAP)
    missing = [c for c in ("code", "earnings_date") if c not in df.columns]
    if missing:
        raise ValueError(f"決算発表予定日一覧に想定した列がありません: {missing}(実際: {list(df.columns)})")
    rows = []
    for _, r in df.iterrows():
        code = str(r["code"]).strip()
        if not code or code.lower() == "nan":
            continue
        raw_date = r.get("earnings_date")
        d = None
        if pd.notna(raw_date):
            ts = pd.to_datetime(raw_date, errors="coerce")
            if pd.notna(ts):
                d = ts.date()
        rows.append(EarningsScheduleRow(code=code, earnings_date=d, estimated=False))
    return rows


def estimate_from_previous_year(con, code: str, as_of: date) -> date | None:
    """前年同時期の実績の決算発表日から、今期の予定日を推定する(未公表のときのフォールバック)。

    calendar テーブルに前年の実績が記録されていなければ None を返す(推定不能)。
    """
    row = con.execute(
        "SELECT earnings_date FROM calendar WHERE code = ? AND earnings_estimated = FALSE", [code]
    ).fetchone()
    if not row or not row[0]:
        return None
    prev = row[0]
    if not isinstance(prev, date):
        return None
    try:
        return prev.replace(year=prev.year + 1)
    except ValueError:
        return prev + timedelta(days=365)


def upsert_earnings(con, rows: list[EarningsScheduleRow]) -> int:
    """calendar テーブルの earnings_date / earnings_estimated だけを更新する
    (ex_rights_date・credit_restriction は他の担当が管理するため触らない)。"""
    n = 0
    now = to_iso(utcnow())
    for row in rows:
        con.execute(
            """INSERT INTO calendar (code, earnings_date, earnings_estimated, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (code) DO UPDATE SET
                 earnings_date = excluded.earnings_date,
                 earnings_estimated = excluded.earnings_estimated,
                 updated_at = excluded.updated_at""",
            [row.code, row.earnings_date.isoformat() if row.earnings_date else None, row.estimated, now],
        )
        n += 1
    return n


def fetch_earnings_schedule(cfg, con, url: str | None = None) -> int:
    """決算発表予定日一覧を取得し、calendar テーブルへ保存する。

    url を渡さない場合は config の collect.earnings_schedule_url を使う。設定が無ければ
    スキップする(fetch_log に記録)。
    """
    collect = cfg.section("collect")
    url = url or collect.get("earnings_schedule_url")
    if not url:
        log_fetch(con, "earnings_schedule", True, 0, "collect.earnings_schedule_url が未設定のためスキップ")
        return 0
    limiter = RateLimiter(
        float(collect.get("request_interval_sec", 1.0)), collect.get("user_agent", "assoc-research/0.1")
    )
    resp = limiter.get(url)
    rows = parse_earnings_schedule(resp.content, filename=url)
    return upsert_earnings(con, rows)
