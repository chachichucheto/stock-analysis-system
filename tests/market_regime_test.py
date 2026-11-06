"""相場の姿勢(D52):株価の事実だけで決める。底打ちは再開の理由にならない。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from assoc.commit.daily import commit_daily
from assoc.config import Thresholds
from assoc.market.regime import market_regime
from assoc.master.names import CompanyMaster
from assoc.report.daily import DayContext, render_daily
from assoc.store.records import RecordStore

TH = Thresholds()
DAY = date(2026, 11, 20)
SAMPLE = Path(__file__).parent / "fixtures" / "daily_output_sample.json"
MASTER = CompanyMaster({"8035": "東京エレクトロン", "6857": "アドバンテスト"}, {})


def _topix(closes):
    n = len(closes)
    return pd.DataFrame({"date": [DAY - timedelta(days=n - 1 - i) for i in range(n)], "close": closes})


def test_calm_market_is_normal():
    r = market_regime(_topix([400 + i * 0.1 for i in range(70)]), {}, DAY, TH)
    assert r["level"] == "通常" and r["reasons"] == []


def test_crash_day_is_investment_halt():
    closes = [400.0] * 69 + [374.0]            # 1日 -6.5%
    r = market_regime(_topix(closes), {}, DAY, TH)
    assert r["level"] == "投資を控える" and any("1日" in x for x in r["reasons"])


def test_bounce_alone_does_not_lift_the_halt():
    # 高値 400 から 340 まで落ち(-15%)、そのあと小さく戻して落ち着いた。株価は落ち着いたが、高値から -12% 以下のまま
    closes = [400.0] * 50 + [380, 360, 340, 338, 341, 344, 346, 347, 348, 349]
    r = market_regime(_topix(closes), {}, DAY, TH)
    assert r["stabilization"]["stabilized"] is True
    assert r["level"] == "投資を控える"
    assert "再開" in r["stabilization"]["caveat"]


def test_many_falling_themes_make_it_cautious():
    tr = {"themes": {f"T{i}": {"n_with_data": 3, "median_5d": -0.12} for i in range(6)}}
    r = market_regime(_topix([400.0] * 70), tr, DAY, TH)
    assert r["level"] == "慎重"


def _stance(verdict, status="未解消", stance="投資を控える"):
    return {"stance": stance, "summary": "選挙とAI規制への懸念で急落",
            "causes": [{"cause": "民主党のAI・データセンター規制", "status": status, "detail": "政策は未定", "next_check": "11/20 議会日程"}],
            "resume": {"verdict": verdict, "reason": "理由が未解消", "conditions": ["規制の方向が出る"]}}


def test_resume_is_not_allowed_while_a_cause_is_unresolved(tmp_path):
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    doc["market_stance"] = _stance("可")
    store = RecordStore(tmp_path)
    r = commit_daily(doc, store, MASTER)
    assert r.ok and r.counts["market_stance"] == 1
    assert list(store.read("market_stance"))[0]["resume"]["verdict"] == "まだ"     # 投資を控える+未解消 → まだ
    assert any("投資再開" in w for w in r.warnings)


def test_resume_allowed_when_all_resolved(tmp_path):
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    doc["market_stance"] = _stance("可", status="解消", stance="慎重")
    store = RecordStore(tmp_path)
    assert commit_daily(doc, store, MASTER).ok
    assert list(store.read("market_stance"))[0]["resume"]["verdict"] == "可"


def test_report_shows_unresolved_causes_and_machine_disagreement():
    regime = {"level": "投資を控える", "reasons": ["TOPIX が1日で -6.5%"],
              "stabilization": {"stabilized": False, "notes": ["✗ 直近安値から 0 営業日"]}}
    text = render_daily(DAY, [], DayContext(market_stance=_stance("まだ", stance="通常"), regime=regime))
    assert "下げた理由は解消されたか" in text and "🔴 未解消" in text and "🔴 まだ再開しない" in text
    assert "機械の判定(株価の事実)は「投資を控える」" in text


def test_exit_call_with_severity_and_alternatives_commits(tmp_path):
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    doc["exit_calls"] = [{"target_type": "銘柄", "target": "2914 日本たばこ産業", "action": "撤退", "severity": "中", "scope": "個社",
                          "reason": "ロシア事業の接収で、海外の資産が国家に奪われうることが示された", "trigger": "11/5 ロシアが子会社を接収",
                          "reentry_conditions": "接収が撤回される、または補償の道筋が出る", "wrong_if": "接収の影響が利益の5%未満と開示される",
                          "alternatives": [{"code": "8035", "company_name": "東京エレクトロン", "why": "例"},
                                           {"code": "9999", "company_name": "存在しない", "why": "x"}]}]
    store = RecordStore(tmp_path)
    r = commit_daily(doc, store, MASTER)
    rec = list(store.read("exit_call"))[0]
    assert r.ok and [a["code"] for a in rec["alternatives"]] == ["8035"] and rec["severity"] == "中"
