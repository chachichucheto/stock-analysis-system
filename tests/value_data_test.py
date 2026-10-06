from datetime import date

import pytest

from assoc.value.data import FinancialsStore, Universe
from value_helpers import fin_row, store, universe_row

HEADER = "code,period_end,disclosed_date,period,cash,receivables,total_liabilities,shares_ex_treasury\n"


def test_financials_csv_roundtrip_and_blank_is_none(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text(HEADER + "1000,2026-03-31,2026-05-15,FY,\"5,000\",,200,1000\n", encoding="utf-8")
    s = FinancialsStore.from_csv(p)
    r = s.known_rows("1000", date(2026, 6, 1))[0]
    assert r.cash == 5000.0 and r.receivables is None and r.total_liabilities == 200.0


def test_financials_csv_errors_are_actionable(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("code,period_end\n1000,2026-03-31\n", encoding="utf-8")
    with pytest.raises(ValueError, match="必要な列がありません"):
        FinancialsStore.from_csv(p)
    p.write_text(HEADER + "1000,2026-03-31,2026-05-15,XX,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="period"):
        FinancialsStore.from_csv(p)
    p.write_text(HEADER + "1000,2026/03/31,2026-05-15,FY,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        FinancialsStore.from_csv(p)


def test_point_in_time_excludes_same_day_and_later_disclosures():
    s = store(fin_row(disclosed=date(2026, 5, 15)))
    assert s.known_rows("1000", date(2026, 5, 15)) == []            # 当日の開示は使わない(引け後に出るため)
    assert len(s.known_rows("1000", date(2026, 5, 16))) == 1
    assert s.known_rows("1000", date(2026, 5, 1)) == []


def test_latest_revision_per_period_wins_but_only_if_already_disclosed():
    a = fin_row(disclosed=date(2026, 5, 15), cash=1.0)
    b = fin_row(disclosed=date(2026, 8, 1), cash=2.0)                # 訂正
    s = store(a, b)
    assert s.known_rows("1000", date(2026, 7, 1))[0].cash == 1.0
    assert s.known_rows("1000", date(2026, 9, 1))[0].cash == 2.0


def test_rows_sorted_newest_period_first_and_year_ago():
    q3 = fin_row(period="Q3", period_end=date(2025, 12, 31), disclosed=date(2026, 2, 10))
    q3_prev = fin_row(period="Q3", period_end=date(2024, 12, 31), disclosed=date(2025, 2, 10))
    fy = fin_row(period="FY", period_end=date(2025, 3, 31), disclosed=date(2025, 5, 10))
    s = store(q3_prev, fy, q3)
    rows = s.known_rows("1000", date(2026, 3, 1))
    assert [r.period_end for r in rows] == [date(2025, 12, 31), date(2025, 3, 31), date(2024, 12, 31)]
    assert FinancialsStore.year_ago(rows, rows[0]).period_end == date(2024, 12, 31)
    assert FinancialsStore.year_ago(rows, rows[2]) is None


def test_universe_members_respect_listing_and_delisting(tmp_path):
    u = Universe([universe_row("1", listed=date(2020, 1, 1)),
                  universe_row("2", listed=date(2024, 1, 1)),
                  universe_row("3", delisted=date(2023, 6, 1))])
    codes = lambda d: sorted(r.code for r in u.members(d))
    assert codes(date(2022, 1, 1)) == ["1", "3"]
    assert codes(date(2023, 6, 1)) == ["1"]                          # 上場廃止の当日からは含めない
    assert codes(date(2025, 1, 1)) == ["1", "2"]
    assert u.has_delisted() is True
    assert Universe([universe_row("1")]).has_delisted() is False


def test_universe_csv_flags(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("code,name,market,sector33,listed_date,delisted_date,monitoring,going_concern\n"
                 "1000,A社,スタンダード,機械,2001-01-01,,1,0\n", encoding="utf-8")
    r = Universe.from_csv(p).get("1000")
    assert r.monitoring is True and r.going_concern is False and r.delisted_date is None
