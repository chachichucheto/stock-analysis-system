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

【2026-09-30 実確認】配布ページは
`https://www.jpx.co.jp/listing/event-schedules/financial-announcement/index.html`。
月別の Excel(例 `kessan08_0918.xlsx` = 8月に期末・四半期末を迎える会社、9/18更新)が並ぶ。
ファイル名は更新のたびに変わるため、`collect.earnings_schedule_url` にはこのページの URL を設定し、
ページ内のリンクから月別ファイルを取得して統合する。Excel は先頭に題名・「◯年◯月◯日現在」の行があり、
見出し行は5行目。列は 決算発表予定日/コード/会社名/決算期末/業種名/種別/市場区分(日英併記、改行入り)。
**過去の期の行(発表済み)も含まれ、未来の予定は最大でも約1か月先までしか載らない**
(公表されている月のファイルだけが対象)。

ローカルで最初に確認すべき点
----------------------------
- 実際の配布 URL・ファイル形式(Excel か CSV か、列名)。
- 発表予定日が「未定」の会社の表記(空欄か、"未定" という文字列か)。
- 東証以外の市場(名証・福証・札証)の重複コードの扱い。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from assoc.ingest.common import RateLimiter, log_fetch
from assoc.timeutil import jst_date, to_iso, utcnow

_COLUMN_MAP = {"コード": "code", "会社名": "company_name", "発表予定日": "earnings_date"}


@dataclass
class EarningsScheduleRow:
    code: str
    earnings_date: date | None
    estimated: bool = False


def parse_earnings_schedule(raw: bytes, *, filename: str = "schedule.xlsx") -> list[EarningsScheduleRow]:
    """決算発表予定日一覧(Excel)を EarningsScheduleRow のリストにする(純関数)。"""
    engine = "openpyxl" if filename.lower().endswith(".xlsx") else "xlrd"
    raw_df = pd.read_excel(io.BytesIO(raw), engine=engine, header=None, dtype=object)
    # 実ファイルは先頭に題名・「◯年◯月◯日現在」の行があり、見出し行は5行目(2026-09-30 確認)。
    # 「コード」を含むセルのある行を、見出し行として探す。
    header_idx = None
    for i in range(min(len(raw_df), 20)):
        if any(isinstance(v, str) and v.strip().startswith("コード") for v in raw_df.iloc[i].tolist()):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("決算発表予定日一覧に「コード」列の見出し行が見つかりません")
    header = [str(v).strip() if isinstance(v, str) else "" for v in raw_df.iloc[header_idx].tolist()]
    code_col = next((j for j, h in enumerate(header) if h.startswith("コード")), None)
    date_col = next((j for j, h in enumerate(header) if "予定日" in h), None)
    if code_col is None or date_col is None:
        raise ValueError(f"決算発表予定日一覧に想定した列がありません(実際: {header})")
    rows = []
    for _, r in raw_df.iloc[header_idx + 1:].iterrows():
        code_v = r.iloc[code_col]
        code = str(code_v).strip() if pd.notna(code_v) else ""
        if not code or code.lower() == "nan":
            continue
        if code.endswith(".0") and code[:-2].isdigit():      # 数値セルは float になり得る
            code = code[:-2]
        raw_date = r.iloc[date_col]
        d = None
        if pd.notna(raw_date):
            ts = pd.to_datetime(raw_date, errors="coerce")
            if pd.notna(ts):
                d = ts.date()
        rows.append(EarningsScheduleRow(code=code, earnings_date=d, estimated=False))
    return rows


_KESSAN_LINK_RE = re.compile(r'href="([^"]*kessan\d+_\d+\.xlsx?)"', re.IGNORECASE)


def find_schedule_links(page_html: str, page_url: str) -> list[str]:
    """配布ページの HTML から、決算発表予定日の Excel(月別)へのリンクを絶対 URL で返す(純関数)。

    ファイル名は更新のたびに変わる(例 `kessan08_0918.xlsx` = 8月期末分・9/18更新)ため、
    固定 URL は置かず、ページからリンクを拾う。
    """
    from urllib.parse import urljoin

    return [urljoin(page_url, m) for m in dict.fromkeys(_KESSAN_LINK_RE.findall(page_html))]


def merge_schedule_rows(all_rows: list[EarningsScheduleRow], today: date) -> list[EarningsScheduleRow]:
    """月別ファイルをまたいで同じコードが複数出たとき、今日以降で最も近い日付の行を残す
    (今日以降が無ければ最も新しい日付)。日付が空の行は日付のある行に負ける。"""
    best: dict[str, EarningsScheduleRow] = {}
    for row in all_rows:
        cur = best.get(row.code)
        if cur is None or _preferred(row, cur, today):
            best[row.code] = row
    return list(best.values())


def _preferred(new: EarningsScheduleRow, cur: EarningsScheduleRow, today: date) -> bool:
    if new.earnings_date is None:
        return False
    if cur.earnings_date is None:
        return True
    new_future, cur_future = new.earnings_date >= today, cur.earnings_date >= today
    if new_future != cur_future:
        return new_future
    if new_future:
        return new.earnings_date < cur.earnings_date      # どちらも未来: 近い方
    return new.earnings_date > cur.earnings_date          # どちらも過去: 新しい方


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
    if url.lower().endswith((".html", ".htm")):
        # 配布ページが指定された場合: ページ内の月別 Excel をすべて取得して統合する
        html = resp.content.decode("utf-8", errors="replace")
        links = find_schedule_links(html, url)
        if not links:
            raise ValueError("配布ページに決算発表予定日の Excel へのリンクが見つかりません")
        all_rows: list[EarningsScheduleRow] = []
        for link in links:
            all_rows.extend(parse_earnings_schedule(limiter.get(link).content, filename=link))
        rows = merge_schedule_rows(all_rows, jst_date(utcnow()))
    else:
        rows = parse_earnings_schedule(resp.content, filename=url)
    return upsert_earnings(con, rows)
