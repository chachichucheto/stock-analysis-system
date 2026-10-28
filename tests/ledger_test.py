from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb

from assoc.events.companies import CompanyIndex
from assoc.events.ledger import build_ledger
from assoc.master.names import CompanyMaster

JST = ZoneInfo("Asia/Tokyo")
NAMES = {"9449": "ＧＭＯインターネットグループ", "7203": "トヨタ自動車", "4684": "オービック", "5909": "コロナ"}
DAY = date(2026, 10, 28)


def _con(news, disclosures=()):
    con = duckdb.connect()
    con.execute("CREATE TABLE news_item (title VARCHAR, source VARCHAR, summary VARCHAR, url VARCHAR, "
                "first_observed_at TIMESTAMPTZ)")
    con.execute("CREATE TABLE disclosure (title VARCHAR, code VARCHAR, url VARCHAR, first_observed_at TIMESTAMPTZ)")
    for t, s, d in news:
        con.execute("INSERT INTO news_item VALUES (?,?,?,?,?)",
                    [t, s, "", "http://x", datetime(2026, 10, d, 12, 0, tzinfo=JST)])
    for t, c, d in disclosures:
        con.execute("INSERT INTO disclosure VALUES (?,?,?,?)", [t, c, "http://y", datetime(2026, 10, d, 15, 0, tzinfo=JST)])
    return con


def _index():
    return CompanyIndex.from_master(CompanyMaster(NAMES), {"GMO": "9449", "トヨタ": "7203"}, {"コロナ"})


def test_different_material_types_across_days_are_stacked():
    con = _con([("トヨタ、通期業績予想を上方修正", "NHK 経済", 24), ("トヨタ、米社から大型受注", "朝日新聞 経済", 28)])
    led = build_ledger(con, _index(), DAY, liquid=None, names=NAMES)
    assert [e["code"] for e in led] == ["7203"]
    assert {m["type"] for m in led[0]["materials"]} == {"業績修正", "受注・契約"}


def test_same_day_overlap_and_old_material_are_not_listed():
    same_day = _con([], [("業績予想の修正に関するお知らせ", "4684", 28), ("配当予想の修正に関するお知らせ", "4684", 28)])
    assert build_ledger(same_day, _index(), DAY, liquid=None, names=NAMES) == []
    old = _con([("トヨタ、通期業績予想を上方修正", "NHK 経済", 15), ("トヨタ、米社から大型受注", "朝日新聞 経済", 20)])
    assert build_ledger(old, _index(), DAY, liquid=None, names=NAMES) == []     # 直近2日に新しい材料が無い


def test_long_running_story_with_many_media_is_listed_and_illiquid_is_dropped():
    news = [("GMO、不正アクセスの被害を発表", m, d) for d in (24, 26, 28) for m in ("NHK 社会", "朝日新聞 社会", "日経 社会")]
    con = _con(news)
    led = build_ledger(con, _index(), DAY, liquid=None, names=NAMES)
    assert [e["code"] for e in led] == ["9449"] and led[0]["reason"] == "同じ材料が続いている"
    assert build_ledger(con, _index(), DAY, liquid={"7203"}, names=NAMES) == []
