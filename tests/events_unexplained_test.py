"""原因が分からない急騰・急落銘柄の検出(events/unexplained.py。D44)。

背景:2026-10-15 に住友ファーマが+15.8%(出来高は普段の約5倍)になったが、見出し・開示に社名が無く、判定に出なかった。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from assoc.app import App
from assoc.config import Config, Thresholds
from assoc.events.unexplained import detect_unexplained_movers
from assoc.timeutil import JST

TH = Thresholds()
DAY = date(2026, 10, 17)


@pytest.fixture
def app(tmp_path):
    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "csv_dir"}, "collect": {}})
    return App(cfg)


def _days(n=30):
    d, out = date(2026, 10, 16), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _gap(tmp_path, jump: dict[str, float], vol_mult: float = 5.0):
    """TOPIX(1306)と、銘柄3つ。最終日(10/16)に、指定の銘柄だけが jump の割合で動く。"""
    rows = []
    import random
    rnd = random.Random(1)
    days = _days()
    for code in ("1306", "4506", "7203", "8306"):
        price = 1000.0
        for i, d in enumerate(days):
            r = rnd.uniform(-0.008, 0.008)
            vol = 1_000_000
            if d == days[-1] and code in jump:
                r, vol = jump[code], int(vol * vol_mult)
            price *= 1 + r
            rows.append({"code": code, "date": d, "open": price, "high": price, "low": price, "close": price, "volume": vol})
    path = tmp_path / "gap.parquet"
    import duckdb
    df = pd.DataFrame(rows)
    con = duckdb.connect()
    try:
        con.register("df", df)
        con.execute("COPY (SELECT * FROM df) TO ? (FORMAT PARQUET)".replace("?", f"'{path.as_posix()}'"))
    finally:
        con.close()
    return path


def _title(app, text):
    obs = datetime.combine(date(2026, 10, 16), time(18), tzinfo=JST)
    app.con.execute("INSERT INTO news_item VALUES ('n1', 'rss', 2, ?, '', 'https://example.com/1', 'ja', ?, ?, 'h1')", [text, obs, obs])


NAMES = {"4506": "住友ファーマ", "7203": "トヨタ自動車", "8306": "三菱ＵＦＪフィナンシャル・グループ"}


def test_big_move_without_headline_is_flagged(app, tmp_path):
    th = TH.__class__(min_turnover_yen=1.0)
    res = detect_unexplained_movers(app.con, _gap(tmp_path, {"4506": 0.16}), DAY, th, NAMES, "1306")
    assert [i["code"] for i in res["items"]] == ["4506"]
    assert res["items"][0]["volume_ratio"] >= 4 and res["items"][0]["name"] == "住友ファーマ"


def test_move_with_company_headline_is_explained(app, tmp_path):
    _title(app, "住友ファーマ、iPS細胞由来の治療薬で新たな発表")
    th = TH.__class__(min_turnover_yen=1.0)
    res = detect_unexplained_movers(app.con, _gap(tmp_path, {"4506": 0.16}), DAY, th, NAMES, "1306")
    assert res["items"] == [] and res["explained_count"] == 1


def test_move_without_volume_surge_is_ignored(app, tmp_path):
    th = TH.__class__(min_turnover_yen=1.0)
    res = detect_unexplained_movers(app.con, _gap(tmp_path, {"4506": 0.16}, vol_mult=1.0), DAY, th, NAMES, "1306")
    assert res["items"] == []
