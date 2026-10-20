"""連日の被害報道が、入力パックに載ること(D37)の通し試験と、機械の温度判定・足切りの語のテスト。

実際に起きたこと:別々の会社の不正アクセスが連日報じられ、足切りの通過は0件だった。
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

import pytest

from assoc.app import App
from assoc.config import Config, Thresholds
from assoc.events.gate import GateInput, gate_events
from assoc.events.relevance import DEAL_WORDS
from assoc.pack.auto_temperature import auto_signal, should_write
from assoc.timeutil import JST

DAY = date(2026, 10, 7)
TH = Thresholds()


@pytest.fixture(autouse=True)
def _no_real_theme_returns(tmp_path, monkeypatch):
    """本物の knowledge/theme_returns.json(その日の株価)に、テストの結果が左右されないようにする。"""
    from assoc.market import theme_returns
    monkeypatch.setattr(theme_returns, "RETURNS_PATH", tmp_path / "no_theme_returns.json")


@pytest.fixture
def app(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data")},
                      "prices": {"source": "csv_dir", "csv_dir": str(prices), "topix_code": "1306"}, "collect": {}})
    return App(cfg)


_n = 0


def _news(app, title, hour=10, source="ITmedia NEWS", day=DAY):
    global _n
    _n += 1
    obs = datetime.combine(day, time(hour), tzinfo=JST)
    app.con.execute("INSERT INTO news_item VALUES (?, ?, 2, ?, '', ?, 'ja', ?, ?, ?)",
                    [f"b{_n}", source, title, f"https://example.com/{_n}", obs, obs, f"bh{_n}"])


def _breach_day(app, n=30):
    companies = ["大和証券", "損保ジャパン", "HIS", "焼肉きんぐ", "第一生命", "日経新聞", "佐川急便", "ヤマト運輸",
                 "タイムズカー", "ベネフィット・ワン"]
    for i in range(n):
        _news(app, f"{companies[i % len(companies)]}第{i}支店に不正アクセス 個人情報{i}件が漏えいか")


# --- 足切りは1件ごとの判定なので、別々の会社の話は通らない(これが元の不具合) -----------------------------------------

def test_per_event_gate_drops_unrelated_breach_stories_one_by_one():
    hist = [float(i % 5) for i in range(100)]
    events = [GateInput(event_id=f"e{i}", title=f"会社{i}に不正アクセス 個人情報が漏えいか", novelty_hash=f"h{i}",
                        media_count=1.0) for i in range(30)]
    results = gate_events(events, TH, media_history=hist, gdelt_history=[], wiki_history=[])
    assert not any(r.passed for r in results)       # 件数が30件でも、1件ずつは通らない


def test_breach_words_are_investment_relevant():
    for w in ("不正アクセス", "情報漏えい", "ランサム", "サイバー攻撃"):
        assert w in DEAL_WORDS


# --- 入力パックに載る -------------------------------------------------------------------------------------

def test_pack_includes_burst_event_outside_the_gate(app):
    _breach_day(app, 30)
    path = app.prepare(DAY)
    pack = json.loads(path.read_text(encoding="utf-8"))

    assert [b["theme_id"] for b in pack["theme_bursts"]] == ["P06"]
    burst_events = [e for e in pack["events"] if e.get("theme_burst")]
    assert len(burst_events) == 1
    e = burst_events[0]
    assert e["forced_in"] is True and e["theme_id"] == "P06" and e["media_count"] == 30
    assert e["urls"] and e["sample_headlines"]
    assert "4704" in e["codes"] and "4493" in e["codes"]         # サイバーの代表銘柄(1段目)
    # 1件ずつは通らない記事が、まとまって1つのイベントとして渡っている(通常のニュースの枠は使わない)
    assert pack["temperature_update_due"] is True                # 急増したテーマの温度を、週1回を待たず見直す


def test_burst_event_is_recorded_in_event_gate_and_not_mixed_into_attention_history(app):
    _breach_day(app, 30)
    app.prepare(DAY)
    rec = [r for r in app.store.read("event_gate") if r["event_id"] == f"burst-P06-{DAY.isoformat()}"]
    assert len(rec) == 1 and rec[0]["passed"] and rec[0]["forced_in"] and rec[0]["attention_high"] is True
    from assoc.pack.build import _attention_history
    hist = _attention_history(app, DAY + timedelta(days=1))
    assert 30.0 not in hist["media_count"]                       # 急増の件数を、1件ごとの注目度の分布に混ぜない


def test_quiet_day_has_no_burst(app):
    for i in range(5):
        _news(app, f"会社{i}に不正アクセス 情報が漏えいか")
    pack = json.loads(app.prepare(DAY).read_text(encoding="utf-8"))
    assert pack["theme_bursts"] == [] and not any(e.get("theme_burst") for e in pack["events"])


def test_auto_temperature_is_written_and_does_not_reset_the_weekly_llm_clock(app):
    _breach_day(app, 30)
    app.store.append("theme_temperature", {"theme_id": "P06", "temperature": "冷却中", "reason": "LLM",
                                           "run_date": "2026-10-06", "model_id": "m", "prompt_version": "daily-v1"})
    pack = json.loads(app.prepare(DAY).read_text(encoding="utf-8"))
    p06 = next(t for t in pack["themes"] if t["theme_id"] == "P06")
    assert p06["temperature"] == "上昇中" and p06["temperature_source"] == "auto"
    rec = [r for r in app.store.read("theme_temperature") if r.get("source") == "auto"]
    assert len(rec) == 1 and rec[0]["theme_id"] == "P06" and "急増" in rec[0]["reason"]
    # 機械の記録は、LLM の更新の日取りに数えない(最後の LLM の更新は 10/6)
    assert pack["temperature_last_updated"] == "2026-10-06"


def test_auto_temperature_is_not_rewritten_when_unchanged(app):
    _breach_day(app, 30)
    app.prepare(DAY)
    for i in range(30):                                            # 翌日も同じ状態(急増が続く)
        _news(app, f"翌日の{i}社に不正アクセス 情報が漏えい", day=DAY + timedelta(days=1))
    pack = json.loads(app.prepare(DAY + timedelta(days=1)).read_text(encoding="utf-8"))
    assert [b["theme_id"] for b in pack["theme_bursts"]] == ["P06"]
    auto = [r for r in app.store.read("theme_temperature") if r.get("source") == "auto" and r["theme_id"] == "P06"]
    assert len(auto) == 1                                          # 同じ温度を毎晩重ねて書かない


# --- 機械の温度判定 ---------------------------------------------------------------------------------------

def _ps(m1=0.0, t1=0.0, m5=0.0, t5=0.0):
    return {"median_1m": m1, "topix_1m": t1, "median_5d": m5, "topix_5d": t5}


def test_auto_signal_up_on_burst_alone():
    sig = auto_signal(None, {"today_count": 49, "daily": [1] * 7, "elevated_days": 5}, TH)
    assert sig["hint"] == "上昇中" and "急増" in sig["reasons"][0]


def test_auto_signal_up_on_recent_price_surge_without_news():
    assert auto_signal(_ps(m5=0.08, t5=0.01), None, TH)["hint"] == "上昇中"      # 5日の超過 +7%


def test_auto_signal_cooling_needs_both_month_and_week_weak():
    assert auto_signal(_ps(m1=-0.12, t1=0.0, m5=-0.02, t5=0.0), None, TH)["hint"] == "冷却中"
    assert auto_signal(_ps(m1=-0.12, t1=0.0, m5=0.03, t5=0.0), None, TH)["hint"] == "横ばい"   # 直近は戻している


def test_auto_signal_no_data_is_none():
    assert auto_signal(None, None, TH)["hint"] is None


def test_should_write_rules():
    assert should_write("上昇中", {"temperature": "横ばい", "source": "llm"}) is True
    assert should_write("上昇中", {"temperature": "上昇中"}) is False
    assert should_write(None, None) is False
    # 「横ばい」は、直前が自動で書いた温度を戻すときだけ。LLM の温度や未設定の所へは書かない
    assert should_write("横ばい", {"temperature": "上昇中", "source": "llm"}) is False
    assert should_write("横ばい", None) is False
    assert should_write("横ばい", {"temperature": "上昇中", "source": "auto"}) is True


# --- 構造変化の見出しは、媒体数1でも通る(D46) ---------------------------------------------------------------------

def test_structural_headline_passes_with_single_source(app):
    from assoc.events.structural import detect_structural
    _news(app, "インテル、推論向けGPU「Dシリーズ」でエヌビディアに対抗 最大480GBメモリ搭載")
    _news(app, "サムスンとTSMCのHBM提携が半導体株の反発を誘発")
    _news(app, "焼肉きんぐ、新店オープン")
    groups = {"S01": {"theme_id": "P01", "label": "x", "each_of": [["HBM", "ベースダイ"], ["TSMC", "エヌビディア"]]},
              "S02": {"theme_id": "P01", "label": "y", "each_of": [["推論"], ["GPU"], ["エヌビディア"]]}}
    hits = {h.group_id: h for h in detect_structural(app.con, DAY, groups)}
    assert set(hits) == {"S01", "S02"}
    assert "HBM" in hits["S01"].samples[0]["title"]
    # 一度出した見出しは、次回は出さない
    again = detect_structural(app.con, DAY, groups, seen_keys=set(hits["S01"].keys) | set(hits["S02"].keys))
    assert again == []


def test_structural_requires_all_groups(app):
    from assoc.events.structural import detect_structural
    _news(app, "HBM4Eの冷却技術が激突 SEDEXが開幕")
    groups = {"S01": {"theme_id": "P01", "label": "x", "each_of": [["HBM"], ["TSMC", "エヌビディア"]]}}
    assert detect_structural(app.con, DAY, groups) == []
