"""通し試験:見本のニュースと株価で、夕方 → 夜 → 翌日 → 月次の流れが最後まで動くことを確かめる。

外部には接続しない。株価は CSV のフォルダ(prices.source=csv_dir)から読む。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from assoc.app import App
from assoc.config import Config
from assoc.timeutil import add_business_days, is_business_day

DAY0 = date(2026, 10, 1)           # ニュースが出た日(木)


def _days(start: date, n_before: int, n_after: int) -> list[date]:
    d, before = start, []
    while len(before) < n_before:
        d -= timedelta(days=1)
        if is_business_day(d):
            before.append(d)
    after, d = [], start
    while len(after) < n_after:
        if is_business_day(d):
            after.append(d)
        d += timedelta(days=1)
    return sorted(before) + after


def _write_prices(folder, code, dates, closes, volume=2_000_000):
    pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes, "close": closes,
                  "volume": [volume] * len(dates)}).to_csv(folder / f"{code}.csv", index=False)


@pytest.fixture
def app(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    dates = _days(DAY0, 40, 25)
    i0 = dates.index(DAY0)
    flat = [1000.0 + (i % 3) for i in range(len(dates))]             # 小さく揺れる横ばい
    _write_prices(prices, "1306", dates, [2000.0 + (i % 2) for i in range(len(dates))])
    # 1段目:ニュースの日に大きく上がる
    _write_prices(prices, "8035", dates, [c * (1.15 if i >= i0 else 1) for i, c in enumerate(flat)])
    # 2段目:2日後から上がり始め、+20% まで上がる
    _write_prices(prices, "6920", dates,
                  [c * (1 + min(max(i - i0 - 1, 0) * 0.04, 0.20)) for i, c in enumerate(flat)])
    # 2段目(動かない)
    _write_prices(prices, "6146", dates, flat)
    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data"), "backup_dir": str(tmp_path / "backup")},
                      "prices": {"source": "csv_dir", "csv_dir": str(prices), "topix_code": "1306"},
                      "collect": {}})
    a = App(cfg)
    now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    for code, name in [("8035", "東京エレクトロン"), ("6920", "レーザーテック"), ("6146", "ディスコ"), ("1306", "TOPIX連動")]:
        a.con.execute("INSERT INTO universe_daily VALUES (?, ?, ?, 'プライム', '電気機器', '通常', ?)",
                      [DAY0, code, name, now])
    for i, src in enumerate(["NHK", "Yahoo", "Google"]):
        a.con.execute("INSERT INTO news_item VALUES (?, ?, 2, ?, '', ?, 'ja', ?, ?, ?)",
                      [f"n{i}", src, "TSMCが来年の発注を100倍に 半導体製造装置の需要が急増", f"https://example.com/{i}",
                       now, now, f"h{i}"])
    return a


def _llm_output(pack: dict) -> dict:
    """LLM の代わりに、入力パックに合わせた出力を作る。"""
    ev = pack["events"][0]["event_id"]
    return {
        "run": {"date": pack["date"], "mode": "通常", "model_id": "test", "prompt_version": "daily-v1",
                "input_pack_hash": pack["input_pack_hash"]},
        "premise_cards": [],
        "grades": [{"event_id": ev, "provisional_grade": "S", "new_fact": "新しい", "theme_ids": ["P01"],
                    "distance_from_premise": {"summary": "桁違い", "numbers": [{"label": "発注", "premise": "+30%", "new": "100倍"}]},
                    "source_certainty": "公式", "premise_card_version": None, "check_basis": "1段目",
                    "check_codes": ["8035"], "new_theme_flag": False}],
        "scenario_updates": [],
        "scenarios": [{
            "scenario_ref": "new1", "event_id": ev, "statement": "TSMCの発注が100倍 → 装置の需要 → 検査装置が買われる",
            "theme_ids": ["P01"], "new_theme_flag": False, "time_type": "単発", "phase": None, "start_condition": None,
            "scheduled_date": None, "started": True, "end_condition": "発注計画の撤回", "expected_days": 10,
            "strength": "Medium", "expected_rise": {"low": 0.1, "median": 0.25, "high": 0.5, "basis": "S級"},
            "past_case_ids": [],
            "check_plan": [{"arrow_no": 1, "arrow": "発注が増えるか", "indicator": "IR", "source": "会社", "criterion": "明記"}],
            "candidates": [
                {"code": "8035", "company_name": "東京エレクトロン", "stage": 1, "viewpoint": "両方", "side": "long",
                 "rationale": "装置の大手", "break_conditions": ["撤回"], "next_catalyst": "受注", "realization_prob": 0.6,
                 "direction_clear": True},
                {"code": "6920", "company_name": "レーザーテック", "stage": 2, "viewpoint": "投資家", "side": "long",
                 "rationale": "検査装置", "break_conditions": ["撤回"], "next_catalyst": "受注", "realization_prob": 0.5,
                 "direction_clear": True},
                {"code": "6146", "company_name": "ディスコ", "stage": 2, "viewpoint": "経済", "side": "long",
                 "rationale": "切断装置", "break_conditions": ["撤回"], "next_catalyst": "受注", "realization_prob": 0.4,
                 "direction_clear": True},
                {"code": "9999", "company_name": "存在しない会社", "stage": 2, "viewpoint": "経済", "side": "long",
                 "rationale": "x", "break_conditions": ["x"], "next_catalyst": "x", "realization_prob": 0.9,
                 "direction_clear": True}],
            "rejected_candidates": []}],
        "evidence": [{"scenario": "new1", "arrow_no": 1, "url": "https://example.com/ir", "retrieved_at": "2026-10-01T11:00:00Z",
                      "value": "明記", "source_tier": 1, "supports": "for", "published_at": None}],
        "check_status": [{"scenario": "new1", "arrow_no": 1, "status": "確認"}],
    }


def test_full_cycle(app, tmp_path):
    # 夕方:イベント化・足切り・入力パック
    pack_path = app.prepare(DAY0)
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    assert len(pack["events"]) == 1                      # 同じ出来事の3記事が1つのイベントにまとまる
    assert pack["input_pack_hash"]

    # 夜:LLM の出力を確定
    out = app.dir("inbox") / f"daily_{DAY0}.json"
    out.write_text(json.dumps(_llm_output(pack), ensure_ascii=False), encoding="utf-8")
    r = app.commit()
    assert r.ok, r.errors
    assert any("9999" in w for w in r.warnings)          # 存在しない銘柄は確定しない

    # 夜:ランキングとレポート
    md, csv_path = app.report(DAY0)
    text = md.read_text(encoding="utf-8")
    assert "自信度ランキング" in text and "レーザーテック" in text
    picks = [p for p in app.store.read("pick_tracking") if p["event"] == "first_pick"]
    assert picks and all(p["first_pick_date"] == DAY0.isoformat() for p in picks)
    assert csv_path.read_text(encoding="utf-8-sig").startswith("日付")

    # あなたの判断の記録(任意)
    code = picks[0]["code"]
    app.store.append("user_decision", {"scenario_id": picks[0]["scenario_id"], "code": code,
                                       "action": "買う", "reason": "テスト", "decided_at": "2026-10-01T12:00:00+00:00"})
    assert json.loads(app.prepare(add_business_days(DAY0, 1)).read_text(encoding="utf-8"))["temperature_update_due"] is True

    # 翌日以降:等級の確定、「動いた」の判定
    d = DAY0
    for _ in range(8):
        d = add_business_days(d, 1)
        app.nextday(d)
    finals = list(app.store.read("grade_final"))
    assert finals and finals[0]["final_grade"] == "S"   # 1段目が反応したので S のまま
    moved = {p["code"] for p in app.store.read("pick_tracking") if p["event"] == "moved"}
    picked = {p["code"] for p in picks}
    if "6920" in picked:
        assert "6920" in moved                           # 2段目は +20% まで上がった
    assert "6146" not in moved

    # 月次:集計
    review = app.dir("reviews")
    from assoc.review import monthly
    path = monthly.prepare(app, "2026-10")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["metrics"]["picks"] + data["metrics"]["picks_pending"] == len(picks)   # 判定中のものは分母に入れない

    # 記録は書き換えられていない
    assert app.verify() == []
    assert app.backup() is not None
