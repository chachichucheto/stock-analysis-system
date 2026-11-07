"""悪いニュースの量(D53):普段から急に増えたときだけ鳴る。毎日ある悪いニュースでは鳴らない。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from assoc.config import Thresholds
from assoc.market.news_pressure import news_pressure
from assoc.market.regime import market_regime
from assoc.store.db import connect

TH = Thresholds()
DAY = date(2026, 11, 6)


def _con(tmp_path: Path, per_day: dict[date, int]):
    con = connect(tmp_path / "data")
    n = 0
    for d, k in per_day.items():
        for i in range(k):
            n += 1
            t = datetime(d.year, d.month, d.day, 3, 0, tzinfo=timezone.utc)      # JST 12時
            con.execute("INSERT INTO news_item (news_id, source, source_tier, title, url, first_observed_at) VALUES (?,?,?,?,?,?)",
                        [f"n{n}", "テスト", 2, f"株価が急落 事案{d.day}-{i} 世界で株安連鎖", f"https://example.com/{n}", t])
    return con


def _days(counts: list[int]) -> dict[date, int]:
    return {DAY - timedelta(days=len(counts) - 1 - i): c for i, c in enumerate(counts)}


def test_ordinary_bad_news_every_day_does_not_alarm(tmp_path):
    p = news_pressure(_con(tmp_path, _days([15] * 14 + [18])), DAY, TH)
    assert p.level == "" and p.ratio < 2


def test_sudden_surge_alarms_with_headlines(tmp_path):
    p = news_pressure(_con(tmp_path, _days([15] * 14 + [110])), DAY, TH)
    assert p.level == "投資を控える" and p.count == 110 and len(p.headlines) == 8


def test_moderate_surge_is_cautious(tmp_path):
    p = news_pressure(_con(tmp_path, _days([15] * 14 + [35])), DAY, TH)
    assert p.level == "慎重"


def test_news_raises_regime_above_price_but_never_lowers_it(tmp_path):
    import pandas as pd
    flat = pd.DataFrame({"date": [DAY - timedelta(days=69 - i) for i in range(70)], "close": [400.0] * 70})
    news = {"level": "投資を控える", "reasons": ["悪化を示す語を含む見出しが 110 件"]}
    r = market_regime(flat, {}, DAY, TH, news)
    assert r["level"] == "投資を控える" and r["price_level"] == "通常" and any("ニュース" in x for x in r["reasons"])
    crash = pd.DataFrame({"date": flat["date"], "close": [400.0] * 69 + [374.0]})
    r2 = market_regime(crash, {}, DAY, TH, {"level": "慎重", "reasons": []})
    assert r2["level"] == "投資を控える"        # 株価が上なら、ニュースで下げない
