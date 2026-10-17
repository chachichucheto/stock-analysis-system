from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from assoc.config import Config, Thresholds
from assoc.tracking.health import (
    HealthInput, assess, compute_price_metrics, risk_headlines,
)

TH = Thresholds()


def _frame(start: date, closes: list[float], volume: float = 1_000_000.0) -> pd.DataFrame:
    rows, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        rows.append({"date": d, "open": c, "high": c, "low": c, "close": c, "volume": volume})
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def test_healthy_candidate_is_continuing():
    r = assess(HealthInput(excess=0.06, excess_change_5d=0.02, peak_excess=0.06, volume_ratio=1.1, priced_in=0.15,
                           elapsed_days=3, expected_days=10, followup_items=2), TH)
    assert r.status == "継続" and any("維持" in x for x in r.reasons)


def test_excess_falling_below_floor_is_degraded():
    r = assess(HealthInput(excess=-0.06, priced_in=0.0, elapsed_days=3, expected_days=10, followup_items=1), TH)
    assert r.status == "劣化"


def test_pullback_from_peak_is_degraded():
    r = assess(HealthInput(excess=0.02, peak_excess=0.12, priced_in=0.1, elapsed_days=4, expected_days=10, followup_items=1), TH)
    assert r.status == "劣化" and any("押し戻" in x for x in r.reasons)


def test_low_volume_is_caution():
    r = assess(HealthInput(excess=0.03, volume_ratio=0.4, priced_in=0.1, elapsed_days=2, expected_days=10, followup_items=1), TH)
    assert r.status == "注意" and any("売買代金" in x for x in r.reasons)


def test_stalled_without_news_is_caution():
    r = assess(HealthInput(excess=0.0, priced_in=0.05, elapsed_days=6, expected_days=10, followup_items=0), TH)
    assert r.status == "注意" and any("停滞" in x for x in r.reasons)
    # 材料が出ていれば、停滞ではない
    ok = assess(HealthInput(excess=0.0, priced_in=0.05, elapsed_days=6, expected_days=10, followup_items=1), TH)
    assert ok.status == "継続"


def test_risk_headline_alone_is_caution_but_two_with_negative_excess_is_degraded():
    one = assess(HealthInput(excess=0.03, priced_in=0.1, elapsed_days=2, expected_days=10, followup_items=1,
                             risk_items=["A社が計画を撤回"]), TH)
    assert one.status == "注意" and any("要確認" in x for x in one.reasons)
    two = assess(HealthInput(excess=-0.01, priced_in=0.1, elapsed_days=2, expected_days=10, followup_items=2,
                             risk_items=["A社が計画を撤回", "A社に行政処分"]), TH)
    assert two.status == "劣化"


def test_priced_in_and_ended_states():
    assert assess(HealthInput(excess=0.2, priced_in=0.75, elapsed_days=5, expected_days=10, followup_items=1), TH).status == "織り込み進行"
    assert assess(HealthInput(excess=0.2, priced_in=0.3, elapsed_days=5, expected_days=10, scenario_ended=True), TH).status == "終了"
    assert assess(HealthInput(excess=0.2, priced_in=0.3, elapsed_days=5, expected_days=10, followup_items=1,
                              scenario_weakened=True), TH).status == "劣化"


def test_degraded_takes_priority_over_priced_in():
    r = assess(HealthInput(excess=-0.08, priced_in=0.8, elapsed_days=5, expected_days=10, followup_items=1), TH)
    assert r.status == "劣化"


def test_risk_headlines_filters_by_words():
    assert risk_headlines(["新工場を発表", "計画を撤回", "業績の下方修正"]) == ["計画を撤回", "業績の下方修正"]


def test_compute_price_metrics_tracks_peak_and_volume():
    start = date(2026, 9, 1)
    topix = _frame(start - timedelta(days=5), [100.0] * 40)
    up_then_down = [100, 100, 102, 106, 112, 118, 116, 112, 108, 105, 104, 103]
    prices = _frame(start - timedelta(days=5), [100.0] * 4 + up_then_down)
    day = prices["date"].iloc[-1]
    m = compute_price_metrics(prices, topix, prices["date"].iloc[4], day)
    assert m["peak_excess"] > m["excess"]                  # ピークから押し戻されている
    assert m["excess_change_5d"] < 0                       # 直近5営業日は下向き
    assert m["volume_ratio"] is not None and 0.9 < m["volume_ratio"] < 1.1     # 出来高一定なら、比は1に近い


def test_compute_price_metrics_with_no_prices_returns_none():
    m = compute_price_metrics(pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]),
                              pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]),
                              date(2026, 9, 1), date(2026, 9, 30))
    assert all(v is None for v in m.values())


def test_dashboard_builds_with_site_nav_and_health_section(tmp_path):
    from assoc.app import App
    from assoc.dashboard.build import build_dashboard

    existing = tmp_path / "existing_dashboards"
    existing.mkdir()
    nav = "".join([
        '<div class="model-nav">',
        '<a href="モデル①株価分析.html">モデル① 株価分析</a>',
        '<a href="モデル②ファンダメンタルズ分析.html">モデル② ファンダメンタルズ分析</a>',
        '<a href="研究_銘柄選定精度.html">研究: 銘柄選定精度</a>',
        '<a href="モデルEx_CIS Engine.html">モデルEx CIS Engine</a>',
        '<a href="../../クロード連想モデル/data/reports/dashboard.html">連想モデル(別フォルダ)</a>',
        '</div>'])
    for name in ("モデル①株価分析.html", "モデル②ファンダメンタルズ分析.html", "モデルEx_CIS Engine.html",
                 "研究_銘柄選定精度.html", "モデル旧⑦スコアリング.html"):
        (existing / name).write_text(f"<html>{nav}</html>", encoding="utf-8")
    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data"), "existing_dashboards": str(existing)},
                      "prices": {"source": "yfinance_live"}})
    html = build_dashboard(App(cfg), date(2026, 9, 30)).read_text(encoding="utf-8")
    assert 'class="model-nav"' in html and "モデル① 株価分析" in html and "モデル⑧ ニュース連想" in html
    assert "連想モデル(別フォルダ)" not in html              # 古い別フォルダのリンクは写さない
    assert "モデル旧⑦" not in html                          # 旧モデルのページはナビゲーションに載せない
    assert "CIS%20Engine.html" in html                       # 空白を含むファイル名もリンクとして壊れない(URL エンコード)
    assert html.index("モデル① 株価分析") < html.index("研究: 銘柄選定精度") < html.index("モデルEx CIS")
    mirror = (existing / "モデル⑧ニュース連想.html").read_text(encoding="utf-8")   # 既存の dashboard フォルダにも置く
    assert 'class="active" href="%E3%83%A2' in mirror and "../" not in mirror.split("</div>")[0]
    assert 'id="health"' in html and "継続性チェック" in html


def test_dashboard_is_split_into_color_coded_tabs_and_explains_confidence(tmp_path):
    """ダッシュボードは4つのタブ(判断・点検と履歴・システム状態・ニュース一覧)に分かれ、自信度の読み方を載せる(D44)。"""
    from assoc.app import App
    from assoc.dashboard.build import build_dashboard

    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "yfinance_live"}})
    html = build_dashboard(App(cfg), date(2026, 9, 30)).read_text(encoding="utf-8")
    for tab in ("judge", "check", "system", "news"):
        assert f'id="tab-{tab}"' in html and f'id="btn-{tab}"' in html
    pane = lambda t: html[html.index(f'id="tab-{t}"'):]
    assert pane("judge").index('id="summary"') < pane("judge").index('id="picks"') < pane("judge").index('id="ranking"') \
        < pane("judge").index('id="scenarios"') < pane("judge").index('id="tab-check"')
    assert pane("check").index('id="health"') < pane("check").index('id="signals"') < pane("check").index('id="history"') \
        < pane("check").index('id="tab-system"')
    assert pane("system").index('id="collect"') < pane("system").index('id="runs"') < pane("system").index('id="tab-news"')
    assert "数字が大きいほど良い" in html
