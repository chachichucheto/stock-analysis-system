from __future__ import annotations

from datetime import date, datetime, time, timedelta

from assoc.store.db import connect
from assoc.timeutil import JST
from assoc.watch import scan

TODAY = date(2026, 9, 30)


def _at(d: date, hour: int = 12) -> datetime:
    return datetime.combine(d, time(hour, 0), tzinfo=JST)


def _seed(con):
    old, new = _at(TODAY - timedelta(days=2)), _at(TODAY)
    # 1301: 新しい開示あり(TOB)、2222: 古い開示だけ、3333: 決算が近い、4444: 社名の短い会社(誤検出しない)
    con.execute("INSERT INTO disclosure VALUES ('d1','tdnet','1301','サンプル物産','公開買付けの開始に関するお知らせ','','https://e.com/d1',?,?)", [new, new])
    con.execute("INSERT INTO disclosure VALUES ('d2','tdnet','2222','サンプル電機','定款の一部変更','','https://e.com/d2',?,?)", [old, old])
    con.execute("INSERT INTO news_item VALUES ('n1','NHK 経済',2,'サンプル物産が新工場を発表','','https://e.com/n1','ja',?,?,'h1')", [new, new])
    con.execute("INSERT INTO news_item VALUES ('n2','google_news:半導体',3,'サンプル物産が新工場を発表','日経','https://e.com/n2','ja',?,?,'h2')", [new, new])
    con.execute("INSERT INTO news_item VALUES ('n3','NHK 経済',2,'日本の景気は堅調','','https://e.com/n3','ja',?,?,'h3')", [new, new])
    con.execute("INSERT INTO calendar (code, earnings_date, earnings_estimated) VALUES ('3333', ?, FALSE)", [TODAY + timedelta(days=3)])
    con.execute("INSERT INTO calendar (code, earnings_date, earnings_estimated) VALUES ('5555', ?, FALSE)", [TODAY + timedelta(days=30)])


def test_scan_finds_only_new_material(tmp_path):
    con = connect(tmp_path)
    _seed(con)
    since = _at(TODAY - timedelta(days=1))
    targets = [{"code": "1301", "company_name": "サンプル物産", "scenario_ids": ["S1"]},
               {"code": "2222", "company_name": "サンプル電機", "scenario_ids": ["S1"]},
               {"code": "3333", "company_name": "サンプル決算", "scenario_ids": ["S2"]},
               {"code": "4444", "company_name": "日本", "scenario_ids": ["S2"]},
               {"code": "5555", "company_name": "遠い決算", "scenario_ids": ["S2"]}]
    by = {r["code"]: r for r in scan(con, targets, since, TODAY)}

    a = by["1301"]
    assert a["new_material"] and len(a["disclosures"]) == 1 and a["disclosures"][0]["strong"] is True
    assert len(a["news"]) == 1                                   # 同じ見出しの重複は1件にまとめる
    assert not by["2222"]["new_material"]                        # 古い開示は数えない
    assert by["3333"]["new_material"] and by["3333"]["earnings_soon"] is True
    assert not by["4444"]["new_material"]                        # 短い社名「日本」では見出しに一致させない
    assert not by["5555"]["new_material"] and by["5555"]["earnings_soon"] is False


def test_scan_with_no_targets_returns_empty(tmp_path):
    assert scan(connect(tmp_path), [], _at(TODAY), TODAY) == []
