from __future__ import annotations

import io
from datetime import date

from assoc.master.calendar import (
    EarningsScheduleRow, find_schedule_links, merge_schedule_rows, parse_earnings_schedule,
)
from assoc.master.universe import parse_monitoring_page


def _real_shaped_xlsx() -> bytes:
    """JPX 決算発表予定日の実ファイルと同じ形(題名・現在日の行のあとに、日英併記の見出し行)の見本。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["８月に四半期末又は期末を迎えた決算発表予定会社の一覧"])
    ws.append(["List of companies scheduled to announce earnings ..."])
    ws.append(["2026年9月17日 現在"])
    ws.append(["As of 2026/9/17"])
    ws.append(["決算発表予定日\nScheduled Dates for Earnings Announcements", "コード\nCode", "会社名", "Issue Name",
               "決算期末\nFiscal Year-end", "業種名", "Industry", "種別", "Fiscal Year/Quarter", "市場区分", "Market Segment"])
    ws.append([date(2026, 9, 15), "2678", "サンプル小売", "SAMPLE", date(2027, 5, 20), "小売業", "Retail", "第１四半期", "First", "プライム", "Prime"])
    ws.append([date(2026, 9, 18), 3544, "サンプル新形式", "SAMPLE2", date(2027, 5, 15), "小売業", "Retail", "第１四半期", "First", "スタンダード", "Standard"])
    ws.append([None, "285A", "日付なし", "X", None, "", "", "", "", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_earnings_schedule_real_shape():
    rows = parse_earnings_schedule(_real_shaped_xlsx(), filename="kessan08_0918.xlsx")
    by_code = {r.code: r for r in rows}
    assert by_code["2678"].earnings_date == date(2026, 9, 15)
    assert by_code["3544"].earnings_date == date(2026, 9, 18)     # 数値セルのコードも文字列にする
    assert by_code["285A"].earnings_date is None                   # 日付が空でも行は残す


def test_find_schedule_links_resolves_relative_urls():
    html = ('<a href="/listing/event-schedules/financial-announcement/tvdivq0000001ofb-att/kessan07_0904.xlsx">7</a>'
            '<a href="/listing/event-schedules/financial-announcement/tvdivq0000001ofb-att/kessan08_0918.xlsx">8</a>'
            '<a href="/other/data.xlsx">無関係</a>')
    links = find_schedule_links(html, "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/index.html")
    assert links == [
        "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/tvdivq0000001ofb-att/kessan07_0904.xlsx",
        "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/tvdivq0000001ofb-att/kessan08_0918.xlsx",
    ]


def test_merge_schedule_rows_prefers_nearest_future():
    today = date(2026, 9, 30)
    rows = [
        EarningsScheduleRow("1111", date(2026, 8, 25)),     # 過去
        EarningsScheduleRow("1111", date(2026, 11, 10)),    # 未来(遠い)
        EarningsScheduleRow("1111", date(2026, 10, 20)),    # 未来(近い) ← 採用
        EarningsScheduleRow("2222", date(2026, 8, 1)),
        EarningsScheduleRow("2222", date(2026, 9, 1)),      # 過去のみ → 新しい方
        EarningsScheduleRow("3333", None),
        EarningsScheduleRow("3333", date(2026, 10, 5)),     # 日付のある行が勝つ
    ]
    got = {r.code: r.earnings_date for r in merge_schedule_rows(rows, today)}
    assert got == {"1111": date(2026, 10, 20), "2222": date(2026, 9, 1), "3333": date(2026, 10, 5)}


_SUPERVISION_HTML = """
<html><body>
<h2>監理銘柄（確認中）</h2>
<table><tr><th>指定年月日</th><th>銘柄名</th><th>コード</th><th>市場区分</th><th>詳細</th><th>備考</th></tr>
<tr><td>2026/10/01</td><td>（株）サンプルA</td><td>9244</td><td>グロース</td><td></td><td></td></tr></table>
<h2>監理銘柄（審査中）</h2>
<table><tr><th>指定年月日</th><th>銘柄名</th><th>コード</th><th>市場区分</th><th>詳細</th><th>備考</th></tr>
<tr><td>2026/10/01</td><td>（株）サンプルB</td><td>8798</td><td>プライム</td><td></td><td></td></tr></table>
<h2>整理銘柄</h2>
<table><tr><th>指定年月日</th><th>銘柄名</th><th>コード</th><th>市場区分</th><th>詳細</th><th>備考</th></tr>
<tr><td>2026/09/30</td><td>（株）サンプルC</td><td>2388</td><td>グロース</td><td></td><td></td></tr>
<tr><td>2026/09/16</td><td>（株）サンプルD</td><td>285A</td><td>スタンダード</td><td></td><td></td></tr></table>
<table><tr><td>見出しの無い表</td><td>無関係</td><td>1234</td></tr></table>
</body></html>
"""


def test_parse_monitoring_page_classifies_by_heading():
    got = parse_monitoring_page(_SUPERVISION_HTML)
    assert got == {"9244": "監理", "8798": "監理", "2388": "整理", "285A": "整理"}
