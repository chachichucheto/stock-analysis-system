"""日中の速報(alerts.py。D38)。"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from assoc import alerts
from assoc.alerts import Alert, fresh, market_hours, price_alerts, run_alerts
from assoc.app import App
from assoc.config import Config, Thresholds
from assoc.timeutil import JST

TH = Thresholds()
DAY = date(2026, 10, 7)
NAMES = {"P06": "サイバー"}


def _series(last_ret_1d: float, ret_5d: float = 0.0, n: int = 10):
    """最後の1日で last_ret_1d、5日前から ret_5d になる日足。"""
    base = 100.0
    s = [base] * n
    s[-6] = base
    s[-1] = base * (1 + ret_5d)
    s[-2] = s[-1] / (1 + last_ret_1d)
    return s


def test_theme_wide_jump_triggers_theme_alert():
    closes = {"4704": _series(0.06), "3692": _series(0.08), "4493": _series(0.07)}
    topix = _series(0.0)
    out = price_alerts(DAY, {"P06": list(closes)}, closes, topix, TH, NAMES)
    assert any(a.kind == "theme_price" and a.key.endswith("theme_day") for a in out)


def test_single_stock_jump_triggers_stock_alert_with_name():
    closes = {"4704": _series(0.01), "4493": _series(0.12)}
    out = price_alerts(DAY, {"P06": ["4704", "4493"]}, closes, _series(0.0), TH, NAMES, {"4493": "サイバーセキュリティクラウド"})
    stock = [a for a in out if a.kind == "stock_price"]
    assert len(stock) == 1 and "サイバーセキュリティクラウド" in stock[0].title


def test_five_day_surge_without_a_big_today_still_alerts():
    closes = {"4704": _series(0.005, ret_5d=0.15), "3692": _series(0.005, ret_5d=0.12)}
    out = price_alerts(DAY, {"P06": list(closes)}, closes, _series(0.0), TH, NAMES)
    assert any(a.key.endswith("theme_5d") for a in out)


def test_quiet_market_has_no_price_alert():
    closes = {"4704": _series(0.01), "3692": _series(-0.01), "4493": _series(0.0)}
    assert price_alerts(DAY, {"P06": list(closes)}, closes, _series(0.005), TH, NAMES) == []


def test_theme_with_a_single_stock_of_data_does_not_alert_as_a_theme():
    closes = {"4704": _series(0.09)}
    out = price_alerts(DAY, {"P06": ["4704", "9999"]}, closes, _series(0.0), TH, NAMES)
    assert not any(a.kind == "theme_price" for a in out)        # 中央値が1銘柄だけでは、テーマの動きと言えない


def test_fresh_dedupes_and_resends_burst_only_on_growth():
    a = Alert(key="2026-10-07:P06:burst", kind="burst", theme_id="P06", title="t", detail="d", level=30)
    assert fresh([a], {}) == [a]
    assert fresh([a], {a.key: 30}) == []
    assert fresh([a], {a.key: 25}) == []                       # 1.5倍未満(30 < 37.5)
    assert fresh([a], {a.key: 18}) == [a]                      # 前回の1.5倍以上に増えた
    p = Alert(key="k", kind="theme_price", theme_id="P06", title="t", detail="d", level=0.9)
    assert fresh([p], {"k": 0.01}) == []                       # 株価の速報は、同じ日に1回だけ


def test_market_hours_weekday_only():
    wed = datetime(2026, 10, 7, 10, 0, tzinfo=JST)
    assert market_hours(wed)
    assert not market_hours(datetime(2026, 10, 7, 20, 0, tzinfo=JST))
    assert not market_hours(datetime(2026, 10, 10, 10, 0, tzinfo=JST))   # 土曜


@pytest.fixture
def app(tmp_path):
    return App(Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "csv_dir"}, "collect": {}}))


def _news(app, i, day=DAY):
    obs = datetime.combine(day, time(10), tzinfo=JST)
    app.con.execute("INSERT INTO news_item VALUES (?, 'ITmedia NEWS', 2, ?, '', ?, 'ja', ?, ?, ?)",
                    [f"a{i}", f"会社{i}に不正アクセス 個人情報が漏えいか", f"https://example.com/{i}", obs, obs, f"ah{i}"])


def test_run_alerts_notifies_once_and_writes_files(app, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_toast", lambda title, body: sent.append((title, body)))
    for i in range(30):
        _news(app, i)
    now = datetime.combine(DAY, time(11), tzinfo=JST).astimezone(timezone.utc)
    first = run_alerts(app, now=now, price=False)
    assert [a.kind for a in first] == ["burst"] and len(sent) == 1
    assert "見出しが急増" in sent[0][1]
    assert (app.data / "alerts" / "latest.md").exists()
    assert len((app.data / "alerts" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    # 1時間後、件数がほぼ同じなら、もう通知しない
    for i in range(30, 33):
        _news(app, i)
    assert run_alerts(app, now=now.replace(hour=12), price=False) == [] and len(sent) == 1
    # 件数が1.5倍以上に増えたら再通知する
    for i in range(33, 50):
        _news(app, i)
    again = run_alerts(app, now=now.replace(hour=13), price=False)
    assert len(again) == 1 and again[0].level >= 45 and len(sent) == 2


def test_run_alerts_quiet_day_sends_nothing(app, monkeypatch):
    monkeypatch.setattr(alerts, "_toast", lambda *a: pytest.fail("通知してはいけない"))
    for i in range(4):
        _news(app, i)
    assert run_alerts(app, now=datetime.combine(DAY, time(11), tzinfo=JST).astimezone(timezone.utc), price=False) == []
