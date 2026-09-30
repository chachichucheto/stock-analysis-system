from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

from assoc.app import App
from assoc.config import Config
from assoc.market.prices import FramePriceSource
from assoc.pipeline.evaluate import PriceLoader
from assoc.timeutil import JST


def _frame(closes: dict[date, float], volume: float = 1_000_000.0) -> pd.DataFrame:
    return pd.DataFrame([{"date": d, "open": c, "high": c, "low": c, "close": c, "volume": volume}
                         for d, c in sorted(closes.items())])


def _weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _app(tmp_path) -> App:
    return App(Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "yfinance_live"}}))


def _seed(app: App):
    app.store.append("scenario", {
        "scenario_id": "S0001", "run_date": "2026-09-01", "started": True, "status": "進行中",
        "statement": "Xが起きた → Yが買われる", "event_id": "e1", "theme_ids": [], "time_type": "単発",
        "strength": "Medium", "expected_days": 10, "end_condition": "崩れたら終了", "new_theme_flag": False,
        "expected_rise": {"low": 0.3, "median": 0.6, "high": 0.9, "basis": "テスト", "version": 1}})
    app.store.append("candidate", {"scenario_id": "S0001", "code": "1111", "company_name": "サンプル社", "stage": 2,
                                   "candidate_id": "c1", "break_conditions": ["撤回"], "next_catalyst": "決算"})
    app.store.append("pick_tracking", {"scenario_id": "S0001", "code": "1111", "event": "first_pick",
                                       "first_pick_date": "2026-09-01", "expected_days": 10, "stage": 2,
                                       "excess_at_pick": 0.0})


def test_record_health_tracks_state_change_over_days(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    days = _weekdays(date(2026, 8, 20), 30)
    idx = {d: i for i, d in enumerate(days)}
    topix = _frame({d: 100.0 for d in days})
    path = {date(2026, 9, 1): 105, date(2026, 9, 2): 110, date(2026, 9, 3): 118, date(2026, 9, 4): 122,
            date(2026, 9, 7): 118, date(2026, 9, 8): 112, date(2026, 9, 9): 108, date(2026, 9, 10): 103}
    stock = {d: (path.get(d, 100.0) if d >= date(2026, 9, 1) else 100.0) for d in days}
    stock_df = _frame(stock)
    src = FramePriceSource({"1111": stock_df, "1306": topix})

    # 9/3: 上昇中で、新しい開示も出ている → 継続
    app.con.execute("INSERT INTO disclosure VALUES ('d1','tdnet','1111','サンプル社','新工場の建設に関するお知らせ','','https://e.com/d1',?,?)",
                    [datetime.combine(date(2026, 9, 3), time(15, 0), tzinfo=JST)] * 2)
    picks = [r for r in app.store.read("pick_tracking") if r["event"] == "first_pick"]
    from assoc.state import load_scenarios
    d1 = date(2026, 9, 3)
    loader = PriceLoader(src, d1)
    n = app._record_health(d1, [], load_scenarios(app.store), loader, loader.get("1306"), picks)
    assert n == 1
    first = app.latest_health(d1)[0]
    assert first["status"] == "継続" and first["changed"] is False and first["is_pick"] is True

    # 同じ日にもう一度実行しても、2回書かない
    assert app._record_health(d1, [], load_scenarios(app.store), loader, loader.get("1306"), picks) == 0

    # 9/10: ピーク(+22%)から +3% まで押し戻された → 劣化(状態が変わった)
    d2 = date(2026, 9, 10)
    loader2 = PriceLoader(src, d2)
    assert app._record_health(d2, [], load_scenarios(app.store), loader2, loader2.get("1306"), picks) == 1
    second = app.latest_health(d2)[0]
    assert second["status"] == "劣化" and second["changed"] is True and second["prev_status"] == "継続"
    assert any("押し戻" in r for r in second["reasons"])
    assert second["metrics"]["peak_excess"] > second["metrics"]["excess"]

    # 入力パックには、劣化した銘柄が載る(深掘りの対象)
    assert [r["code"] for r in app.latest_health(d2)] == ["1111"]
    assert app.store.verify("health_snapshot") == []           # 追記専用の鎖が壊れていない


def test_waiting_scenarios_are_not_checked(tmp_path):
    app = _app(tmp_path)
    app.store.append("scenario", {
        "scenario_id": "S0002", "run_date": "2026-09-01", "started": False, "status": "待機",
        "statement": "局面2で起動", "event_id": "e2", "theme_ids": [], "time_type": "局面展開",
        "strength": "Weak", "expected_days": 10, "end_condition": "x", "new_theme_flag": False,
        "expected_rise": {"low": 0.1, "median": 0.2, "high": 0.4, "basis": "テスト", "version": 1}})
    app.store.append("candidate", {"scenario_id": "S0002", "code": "2222", "company_name": "待機社", "stage": 2,
                                   "candidate_id": "c2", "break_conditions": ["x"], "next_catalyst": "y"})
    from assoc.state import load_scenarios
    day = date(2026, 9, 10)
    loader = PriceLoader(FramePriceSource({}), day)
    assert app._record_health(day, [], load_scenarios(app.store), loader, loader.get("1306"), []) == 0
