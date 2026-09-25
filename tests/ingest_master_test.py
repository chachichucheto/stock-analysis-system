from __future__ import annotations

from datetime import date

from assoc.master.calendar import (
    estimate_from_previous_year,
    fetch_earnings_schedule,
    parse_earnings_schedule,
    upsert_earnings,
)
from assoc.master.universe import fetch_universe, parse_universe_excel, upsert_universe
from ingest_helpers import FIXTURES_DIR, make_config, make_db


def test_parse_universe_excel_extracts_rows():
    rows = parse_universe_excel(FIXTURES_DIR / "jpx_universe_sample.xlsx")
    assert len(rows) == 4
    by_code = {r.code: r for r in rows}
    assert by_code["1301"].company_name == "サンプル物産"
    assert by_code["1301"].market == "プライム（内国株式）"
    assert by_code["1301"].industry33 == "水産・農林業"
    assert all(r.status == "通常" for r in rows)  # 監理・整理は TODO(fetch_monitoring_posts 未実装)


def test_upsert_universe_writes_and_updates(tmp_path):
    con = make_db(tmp_path)
    rows = parse_universe_excel(FIXTURES_DIR / "jpx_universe_sample.xlsx")
    n = upsert_universe(con, rows, date(2026, 9, 25))
    assert n == 4
    saved = con.execute("SELECT count(*) FROM universe_daily WHERE date = '2026-09-25'").fetchone()[0]
    assert saved == 4
    # 再実行しても重複せず上書きされる
    upsert_universe(con, rows, date(2026, 9, 25))
    saved_again = con.execute("SELECT count(*) FROM universe_daily WHERE date = '2026-09-25'").fetchone()[0]
    assert saved_again == 4


def test_fetch_universe_skips_when_url_missing(tmp_path):
    cfg = make_config()
    con = make_db(tmp_path)
    n = fetch_universe(cfg, con)
    assert n == 0
    logged = con.execute("SELECT ok, message FROM fetch_log WHERE source = 'jpx_universe'").fetchall()
    assert logged and logged[0][0] is True
    assert "未設定" in logged[0][1]


def test_parse_earnings_schedule_extracts_rows_and_handles_blank():
    rows = parse_earnings_schedule(
        (FIXTURES_DIR / "earnings_schedule_sample.xlsx").read_bytes(), filename="x.xlsx"
    )
    assert len(rows) == 3
    by_code = {r.code: r for r in rows}
    assert by_code["1301"].earnings_date == date(2026, 11, 10)
    assert by_code["9984"].earnings_date is None  # 未定
    assert all(r.estimated is False for r in rows)


def test_upsert_earnings_does_not_touch_other_columns(tmp_path):
    con = make_db(tmp_path)
    con.execute(
        "INSERT INTO calendar (code, ex_rights_date, credit_restriction) VALUES ('1301', '2026-03-30', '日々公表')"
    )
    rows = parse_earnings_schedule(
        (FIXTURES_DIR / "earnings_schedule_sample.xlsx").read_bytes(), filename="x.xlsx"
    )
    upsert_earnings(con, rows)
    row = con.execute(
        "SELECT earnings_date, ex_rights_date, credit_restriction FROM calendar WHERE code = '1301'"
    ).fetchone()
    assert str(row[0]) == "2026-11-10"
    assert str(row[1]) == "2026-03-30"  # 他の担当が入れた値が残っている
    assert row[2] == "日々公表"


def test_estimate_from_previous_year_uses_recorded_actual(tmp_path):
    con = make_db(tmp_path)
    con.execute(
        "INSERT INTO calendar (code, earnings_date, earnings_estimated) VALUES ('1301', '2025-11-08', FALSE)"
    )
    estimated = estimate_from_previous_year(con, "1301", date(2026, 9, 25))
    assert estimated == date(2026, 11, 8)


def test_estimate_from_previous_year_returns_none_without_history(tmp_path):
    con = make_db(tmp_path)
    assert estimate_from_previous_year(con, "9999", date(2026, 9, 25)) is None


def test_fetch_earnings_schedule_skips_when_url_missing(tmp_path):
    cfg = make_config()
    con = make_db(tmp_path)
    n = fetch_earnings_schedule(cfg, con)
    assert n == 0
    logged = con.execute("SELECT ok, message FROM fetch_log WHERE source = 'earnings_schedule'").fetchall()
    assert logged and logged[0][0] is True
