"""撤退・縮小の目印(D51)。シナリオの状態に関係なく、株価の絶対の動きで出す。"""
from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from assoc.commit.daily import commit_daily, validate_schema
from assoc.config import Thresholds
from assoc.master.names import CompanyMaster
from assoc.report.daily import DayContext, render_daily, render_exit_section
from assoc.store.records import RecordStore
from assoc.tracking.exit_watch import (build_exit_watch, holdings_codes, stock_level, stock_metrics,
                                       theme_exits)

TH = Thresholds()
DAY = date(2026, 11, 6)
SAMPLE = Path(__file__).parent / "fixtures" / "daily_output_sample.json"
MASTER = CompanyMaster({"8035": "東京エレクトロン", "6857": "アドバンテスト"}, {})


def _prices(closes: list[float], end: date = DAY) -> pd.DataFrame:
    n = len(closes)
    days = [end - timedelta(days=n - 1 - i) for i in range(n)]
    return pd.DataFrame({"date": days, "close": closes})


class _Loader:
    def __init__(self, table):
        self.table = table

    def get(self, code):
        return self.table.get(code, pd.DataFrame(columns=["date", "close"]))


def test_stock_level_uses_absolute_moves_not_topix_excess():
    # TOPIX が -7.8% の日に、本命が -30%。TOPIX 超過は -22% だが、絶対の下落で撤退検討になる
    level, reasons = stock_level(-0.30, -0.35, -0.20, -0.078, TH)
    assert level == "撤退検討" and any("相場全体も崩れている" in r for r in reasons)


def test_stock_level_quiet_stock_has_no_flag():
    assert stock_level(0.05, -0.02, 0.01, 0.0, TH)[0] == ""


def test_market_stress_makes_threshold_shallower():
    # 平時は -13% なら縮小検討止まり、相場全体が崩れている日は撤退検討に上がる
    assert stock_level(-0.13, None, None, 0.0, TH)[0] == "縮小検討"
    assert stock_level(-0.13, None, None, -0.07, TH)[0] == "撤退検討"


def test_stock_metrics_since_pick_and_peak():
    p = _prices([100.0] * 10 + [120.0, 110.0, 90.0, 80.0])
    start = p["date"].iloc[9]
    m = stock_metrics(p, start, DAY)
    assert m["since_pick"] == -0.2 and round(m["from_peak"], 3) == round(80 / 120 - 1, 3)


def test_ended_scenario_pick_is_still_watched():
    # シナリオの状態は見ない:pick_tracking の first_pick があれば、終わったシナリオの銘柄も目印が出る
    closes = [100.0] * 25 + [95.0, 90.0, 80.0, 70.0, 65.0, 62.0]
    loader = _Loader({"8035": _prices(closes), "1306": _prices([100.0] * 31)})
    picks = [{"event": "first_pick", "code": "8035", "first_pick_date": (DAY - timedelta(days=10)).isoformat()}]
    tr = {"topix_5d": -0.078, "themes": {"P01": {"n_with_data": 1, "median_5d": -0.22, "median_1m": -0.35, "median_3m": -0.27,
                                                 "stocks": [{"code": "8035", "name": "東京エレクトロン"}]}}}
    w = build_exit_watch(picks, {}, loader, _prices([100.0] * 25 + [99.0, 98.0, 97.0, 95.0, 93.0, 92.0]), DAY,
                         {"8035": "東京エレクトロン"}, tr, {"P01": "AI・半導体"}, TH)
    assert w["tracked_codes"] == 1
    assert w["stocks"][0]["code"] == "8035" and w["stocks"][0]["level"] == "撤退検討"
    assert w["themes"][0]["level"] == "撤退検討"
    assert w["themes"][0]["affected_holdings"][0]["code"] == "8035"


def test_old_picks_are_dropped_after_track_days():
    loader = _Loader({"8035": _prices([100.0] * 25 + [60.0] * 6)})
    picks = [{"event": "first_pick", "code": "8035", "first_pick_date": (DAY - timedelta(days=200)).isoformat()}]
    w = build_exit_watch(picks, {}, loader, _prices([100.0] * 31), DAY, {}, {"themes": {}}, {}, TH)
    assert w["tracked_codes"] == 0 and w["stocks"] == []


def test_holdings_file_adds_stocks_never_picked(tmp_path):
    f = tmp_path / "holdings.yaml"
    f.write_text("codes:\n  - 6857 # アドバンテスト\n  - code: 8035\n    name: 東京エレクトロン\n", encoding="utf-8")
    held = holdings_codes(f)
    assert set(held) == {"6857", "8035"}
    loader = _Loader({"6857": _prices([100.0] * 25 + [90.0, 85.0, 80.0, 75.0, 70.0, 68.0])})
    w = build_exit_watch([], held, loader, _prices([100.0] * 31), DAY, {"6857": "アドバンテスト"}, {"themes": {}}, {}, TH)
    assert w["stocks"][0]["sources"] == ["保有"]


def test_theme_levels():
    tr = {"topix_5d": -0.05, "themes": {
        "A": {"n_with_data": 3, "median_5d": -0.22, "median_1m": -0.35, "median_3m": 0.0, "stocks": []},
        "B": {"n_with_data": 3, "median_5d": -0.12, "median_1m": -0.05, "median_3m": 0.0, "stocks": []},
        "C": {"n_with_data": 3, "median_5d": -0.07, "median_1m": -0.02, "median_3m": 0.0, "stocks": []},
        "D": {"n_with_data": 3, "median_5d": 0.03, "median_1m": 0.05, "median_3m": 0.0, "stocks": []}}}
    got = {t["theme_id"]: t["level"] for t in theme_exits(tr, {}, {}, TH)}
    assert got == {"A": "撤退検討", "B": "縮小検討", "C": "警戒"}


def _call(**kw):
    return {"target_type": "テーマ", "target": "AI・半導体", "action": "撤退", "urgency": "今日中",
            "reason": "データセンター規制で投資が減る", "trigger": "11/4 中間選挙で規制派が勝利",
            "reentry_conditions": "規制法案の見通しが出る", "wrong_if": "主要顧客が投資を維持と発表",
            "codes": [{"code": "8035", "company_name": "東京エレクトロン"}, {"code": "9999", "company_name": "存在しない"}], **kw}


def test_exit_calls_schema_and_commit(tmp_path):
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    doc["exit_calls"] = [_call()]
    assert validate_schema(doc) == []
    store = RecordStore(tmp_path)
    r = commit_daily(doc, store, MASTER)
    assert r.ok and r.counts["exit_call"] == 1
    rec = list(store.read("exit_call"))[0]
    assert [c["code"] for c in rec["codes"]] == ["8035"]          # マスタに無いコードは落とす
    assert any("9999" in w for w in r.warnings)


def test_exit_calls_require_reentry_and_wrong_if():
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    bad = _call()
    del bad["wrong_if"]
    doc["exit_calls"] = [bad]
    assert validate_schema(doc)


def test_old_output_without_exit_calls_still_valid():
    assert validate_schema(json.loads(SAMPLE.read_text(encoding="utf-8"))) == []


def test_report_shows_exit_section_even_when_no_picks():
    watch = {"tracked_codes": 1, "themes": [], "stocks": [{
        "code": "8035", "name": "東京エレクトロン", "level": "撤退検討", "sources": ["本命"], "reasons": ["本命にした日から -34%"],
        "metrics": {"since_pick": -0.34, "from_peak": -0.35, "ret_5d": -0.18}}]}
    text = render_daily(DAY, [], DayContext(exit_calls=[_call()], exit_watch=watch))
    assert "撤退・縮小 1 件" in text and "## 撤退・縮小の提案" in text
    assert "戻ってよい条件" in text and "この判断が外れる条件" in text and "東京エレクトロン(8035・本命)" in text


def test_report_warns_when_machine_flags_not_judged():
    watch = {"tracked_codes": 1, "themes": [], "stocks": [{
        "code": "8035", "name": "東京エレクトロン", "level": "撤退検討", "sources": ["本命"], "reasons": ["x"], "metrics": {}}]}
    assert "夜の判断はこの目印を評価していません" in "\n".join(render_exit_section([], watch))
