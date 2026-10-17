"""Yahoo が制限(429)で空を返し続けるとき、パック作成が止まらないようにする(layered_prices。D43)。"""
from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
import pytest

from assoc.market import layered_prices as lp


@pytest.fixture
def fake_yf(monkeypatch):
    calls = {"n": 0}
    mod = types.ModuleType("yfinance")
    mod.download = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or pd.DataFrame())
    mod.set_tz_cache_location = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    monkeypatch.setattr(lp.time, "sleep", lambda _s: None)
    lp.reset_yahoo_budget()
    yield calls
    lp.reset_yahoo_budget()


def test_gives_up_after_consecutive_empty_downloads(fake_yf):
    for _ in range(10):
        lp._download_chunk(["7203.T"], date(2026, 10, 1), date(2026, 10, 16))
    # 3回空振り(各2回試行)で諦め、以降は Yahoo に問い合わせない
    assert fake_yf["n"] == lp.YAHOO_MAX_EMPTY_STREAK * 2
    assert lp.yahoo_gave_up()


def test_budget_exhaustion_stops_calls(fake_yf, monkeypatch):
    monkeypatch.setattr(lp, "YAHOO_BUDGET_SECONDS", 0.0)
    assert lp.yahoo_gave_up()
    assert lp._download_chunk(["7203.T"], date(2026, 10, 1), date(2026, 10, 16)).empty
    assert fake_yf["n"] == 0
