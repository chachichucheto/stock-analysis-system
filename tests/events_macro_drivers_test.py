"""マクロの材料 → 影響先のテーマ、資金の移動の表(events/macro_drivers.py。D41)。"""
from __future__ import annotations

from datetime import date, datetime, time

import pytest

from assoc.app import App
from assoc.config import Config
from assoc.events.macro_drivers import detect_macro_drivers, theme_rotation
from assoc.timeutil import JST

DAY = date(2026, 10, 17)


@pytest.fixture
def app(tmp_path):
    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "csv_dir"}, "collect": {}})
    return App(cfg)


def _add(app, n, title):
    obs = datetime.combine(DAY, time(8), tzinfo=JST)
    app.con.execute("INSERT INTO news_item VALUES (?, 'rss', 2, ?, '', ?, 'ja', ?, ?, ?)",
                    [f"m{n}", title, f"https://example.com/{n}", obs, obs, f"h{n}"])


TR = {"topix_5d": -0.003, "as_of": "2026-10-16", "themes": {
    "M15": {"median_5d": -0.006, "n_with_data": 3, "median_1m": -0.05, "median_3m": -0.03,
            "stocks": [{"code": "8306", "name": "三菱UFJ", "ret_5d": -0.008}, {"code": "8766", "name": "東京海上", "ret_5d": 0.078}]},
    "P06": {"median_5d": 0.134, "n_with_data": 4, "median_1m": 0.9, "median_3m": 1.0,
            "stocks": [{"code": "4704", "name": "トレンド", "ret_5d": 0.016}, {"code": "4493", "name": "CSC", "ret_5d": 0.134}]}}}
MAP = {"rates": {"name": "利上げ観測", "words": ["利上げ"], "min_count": 2,
                 "effects": [{"theme": "M15", "dir": "up", "why": "利ざや"}]}}


def test_rotation_lists_rising_theme_laggards():
    r = theme_rotation(TR, {"P06": "セキュリティ"})
    assert [x["theme_id"] for x in r["rising"]] == ["P06"]
    assert [l["code"] for l in r["rising"][0]["laggards"]] == ["4704"]   # 中央値の半分に満たない銘柄


def test_driver_found_and_unmoved_stock_listed(app):
    _add(app, 1, "日銀 追加利上げの観測が強まる")
    _add(app, 2, "米FRB 早期利上げ観測が高まる")
    out = detect_macro_drivers(app.con, DAY, TR, MAP)
    assert [d["driver_id"] for d in out] == ["rates"]
    e = out[0]["effects"][0]
    assert e["already_moved"] is False
    assert [u["code"] for u in e["unmoved_stocks"]] == ["8306"]   # 東京海上はすでに動いた


def test_driver_needs_enough_headlines(app):
    _add(app, 3, "日銀 追加利上げの観測")
    assert detect_macro_drivers(app.con, DAY, TR, MAP) == []
