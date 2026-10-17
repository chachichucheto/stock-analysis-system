"""株価の大きな動きから作るイベント(events/price_shock.py。D40)。

背景:2026-10-17、日経平均3000円安・米CPI上振れ・AI半導体株の急落が、報道の量で測る足切りを全部すり抜けた。
"""
from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import pytest

from assoc.app import App
from assoc.config import Config, Thresholds
from assoc.events.price_shock import detect_price_shocks, topix_stats
from assoc.timeutil import JST

TH = Thresholds()
DAY = date(2026, 10, 17)


@pytest.fixture
def app(tmp_path):
    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "csv_dir"}, "collect": {}})
    return App(cfg)


def _add(app, n: int, title: str):
    obs = datetime.combine(DAY, time(8), tzinfo=JST)
    app.con.execute("INSERT INTO news_item VALUES (?, 'rss', 2, ?, '', ?, 'ja', ?, ?, ?)",
                    [f"n{n}", title, f"https://example.com/{n}", obs, obs, f"h{n}"])


def _tr(median_5d, topix_5d=-0.003, n=5):
    return {"topix_5d": topix_5d, "themes": {"P01": {"median_5d": median_5d, "n_with_data": n}}}


def test_topix_stats_worst_day():
    df = pd.DataFrame({"close": [100, 100, 99, 96.6, 97, 98, 97.5]})
    s = topix_stats(df)
    assert s["worst_day"] == pytest.approx(96.6 / 99 - 1)


def test_theme_shock_when_median_far_from_topix(app):
    _add(app, 1, "半導体株が急落")
    out = detect_price_shocks(app.con, DAY, TH, _tr(-0.128), None,
                              triggers={"P01": {"words": ["半導体"]}}, market_words=["急落"])
    assert [s.shock_id for s in out] == ["P01"]
    assert out[0].samples and "半導体" in out[0].samples[0]["title"]


def test_no_shock_for_small_or_thin_moves(app):
    assert detect_price_shocks(app.con, DAY, TH, _tr(-0.05), None, triggers={}, market_words=[]) == []
    assert detect_price_shocks(app.con, DAY, TH, _tr(-0.20, n=2), None, triggers={}, market_words=[]) == []


def test_market_shock_from_one_bad_day_even_if_recovered(app):
    _add(app, 2, "日経平均終値、3318円安い")
    df = pd.DataFrame({"close": [100, 100, 100, 97.6, 99.5, 99.7]})   # 1日 -2.4% のあと戻した
    out = detect_price_shocks(app.con, DAY, TH, {"themes": {}}, df, triggers={}, market_words=["日経平均"])
    assert [s.shock_id for s in out] == ["market"]
    assert out[0].samples
