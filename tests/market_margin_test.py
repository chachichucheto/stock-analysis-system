from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from openpyxl import Workbook

from assoc.market import margin
from ingest_helpers import make_db

HEADER_ROWS = 31


def _row(flag, name, market, loan, code, sell, sell_chg, buy, buy_chg, sell_pct="1.0", buy_pct="1.0"):
    ratio = f"{sell / buy * 100:.1f}" if buy else "0.0"
    return ["B", flag, "", name, market, loan, code, "JP3000000001", sell, sell_chg, sell_pct,
            buy, buy_chg, buy_pct, ratio, 0, 0, sell, sell_chg, 0, 0, buy, buy_chg]


def make_xlsx(path, as_of: str, rows: list[list]):
    """JPX の mtdaily.xlsx と同じ配置の見本を作る(注記行 + 見出し行 + 銘柄行)。"""
    ws = Workbook().active
    for _ in range(25):
        ws.append([None])
    ws.append([None, as_of, None, "申込み現在"])
    for _ in range(HEADER_ROWS - 26):
        ws.append([None])
    for r in rows:
        ws.append(r)
    ws.parent.save(path)
    return path


ROWS_D1 = [
    _row("規", "テストA 普通株式", "スタンダード", "貸", "44400", 28000, -2000, 240000, -7000, "1.0", "5.7"),
    _row("日", "テストB 普通株式", "プライム", "制", "283A0", 100, 0, 5000, 0, "0.0", "0.0"),
    ["B", "", "", "テストETF 受益証券", "ETF", "貸", "16330", "JP3046720003", 264, -2, "*", 491, 0, "*", "53.8",
     0, 0, 264, -2, 0, 0, 491, 0],
    [None] * 23,  # 注記・空行は無視される
]


def test_parse_daily_excel_reads_rows_and_normalizes_codes(tmp_path):
    df = margin.parse_daily_excel(make_xlsx(tmp_path / "a.xlsx", "2026/10/6", ROWS_D1))
    assert list(df["code"]) == ["4440", "283A", "1633"]
    assert set(df["date"]) == {date(2026, 10, 6)}
    a = df.iloc[0]
    assert (a["sell_bal"], a["sell_chg"], a["buy_bal"], a["buy_chg"]) == (28000, -2000, 240000, -7000)
    assert a["buy_listed_pct"] == 5.7 and a["flags"] == "規" and a["loan_type"] == "貸"
    assert pd.isna(df.iloc[2]["buy_listed_pct"])      # ETF の上場比 "*" は欠損


def test_parse_daily_excel_rejects_unknown_layout(tmp_path):
    ws = Workbook().active
    ws.append(["関係のない表"])
    ws.parent.save(tmp_path / "x.xlsx")
    with pytest.raises(ValueError, match="基準日"):
        margin.parse_daily_excel(tmp_path / "x.xlsx")


def test_num_handles_japanese_negative_and_blanks():
    assert margin._num("▲1,200") == -1200
    assert margin._num("*") is None and margin._num(None) is None
    assert margin._num("12.5") == 12.5


def test_find_daily_url_picks_latest_and_resolves_relative():
    html = ('<a href="/m/x-att/20261005_mtdaily.xlsx">a</a><a href="/m/x-att/20261006_mtdaily.xlsx">b</a>'
            '<a href="/m/x-att/20261006_mtdaily.pdf">c</a>')
    assert margin.find_daily_url(html, "https://www.jpx.co.jp/m/index.html") == \
        "https://www.jpx.co.jp/m/x-att/20261006_mtdaily.xlsx"
    with pytest.raises(ValueError):
        margin.find_daily_url("<html></html>")


def _hist(tmp_path, days: int):
    """days 日分、同じ2銘柄で買残が毎日 +20% ずつ増える履歴を DB に入れて返す。"""
    con = make_db(tmp_path)
    for i in range(days):
        buy = round(240000 * 1.2 ** i)
        rows = [_row("規", "テストA 普通株式", "スタンダード", "貸", "44400", 28000, 0, buy, buy - round(buy / 1.2)),
                _row("日", "ノイズ 普通株式", "スタンダード", "貸", "99990", 10, 5, 100, 50, "0.0", "0.0")]
        df = margin.parse_daily_excel(make_xlsx(tmp_path / f"{i}.xlsx", f"2026/10/{i + 1}", rows))
        margin.upsert_margin(con, df)
    return con


def test_upsert_is_idempotent(tmp_path):
    con = _hist(tmp_path, 1)
    df = margin.parse_daily_excel(make_xlsx(tmp_path / "again.xlsx", "2026/10/1", ROWS_D1))
    margin.upsert_margin(con, df)
    margin.upsert_margin(con, df)
    assert con.execute("SELECT count(*) FROM margin_daily WHERE date = '2026-10-01'").fetchone()[0] == 4


def test_build_metrics_computes_ratios_and_signals(tmp_path):
    con = _hist(tmp_path, 7)
    m = margin.build_metrics(margin.load_history(con)).set_index("code")
    a = m.loc["4440"]
    assert a["date"] == date(2026, 10, 7)
    assert a["ratio_bs"] == pytest.approx(a["buy_bal"] / 28000)
    assert a["buy_chg_pct"] == pytest.approx(0.2, abs=0.01)
    assert a["buy_nd_pct"] == pytest.approx(1.2 ** 5 - 1, abs=0.01)     # 5営業日前からの増減
    assert a["buy_pctile"] == 1.0                                          # 履歴の最大
    assert "買残急増" in a["signals"]
    assert m.loc["9999", "signals"] == ""        # 上場比 0% の極小残高は、増減率が大きくても見立てを付けない


def test_build_metrics_short_history_leaves_window_metrics_empty(tmp_path):
    m = margin.build_metrics(margin.load_history(_hist(tmp_path, 2))).set_index("code")
    assert pd.isna(m.loc["4440", "buy_nd_pct"]) and pd.isna(m.loc["4440", "buy_pctile"])


def test_days_to_clear_uses_price_copy_volume(tmp_path):
    con = _hist(tmp_path, 1)
    for i in range(12):
        con.execute("INSERT INTO price_copy (code, date, volume) VALUES ('4440', ?, 10000)",
                    [date(2026, 9, 1 + i)])
    vol = margin.load_avg_volume(con, date(2026, 10, 1))
    assert vol == {"4440": 10000.0}
    m = margin.build_metrics(margin.load_history(con), avg_volume=vol).set_index("code")
    assert m.loc["4440", "days_to_clear"] == pytest.approx(m.loc["4440", "buy_bal"] / 10000)


def test_write_report_creates_files_and_handles_empty(tmp_path):
    con = make_db(tmp_path / "empty")
    assert margin.write_report(con, tmp_path / "out") is None
    con = _hist(tmp_path, 7)
    md, csv = margin.write_report(con, tmp_path / "out")
    assert md.name == "margin_2026-10-07.md" and csv.exists()
    text = md.read_text(encoding="utf-8")
    assert "テストA" in text and "買残急増" in text and "ノイズ" not in text
