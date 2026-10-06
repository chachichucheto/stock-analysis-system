"""過去検証のテスト。「効果を仕込んだ世界」で検出できること、先読み・生存者の罠にはまらないことを確かめる。"""
from datetime import date

import pandas as pd
import pytest

from assoc.market.prices import FramePriceSource
from assoc.timeutil import add_business_days, is_business_day
from assoc.value import backtest as bt
from assoc.value.data import Universe
from assoc.value.thresholds import ValueThresholds
from value_helpers import fin_row, frame, plain_fin, store, universe_row

TH = ValueThresholds(permutation_draws=500)
START, END = date(2023, 6, 1), date(2026, 3, 31)


def bdays(start, end):
    d, out = start, []
    while d <= end:
        if is_business_day(d):
            out.append(d)
        d = d.fromordinal(d.toordinal() + 1)
    return out


DAYS = bdays(date(2021, 1, 4), date(2026, 8, 31))
TEST_DATES = bt.month_end_dates(date(2024, 6, 1), date(2026, 3, 31))
TOPIX = pd.DataFrame({"date": DAYS, "close": [1000.0] * len(DAYS)})


def wave_prices(base: float, asof_dates: list[date]) -> list[float]:
    """各基準日の翌15営業日で +20% まで上がり、その翌日に元へ戻る価格(「動く」世界)。
    次の基準日までに必ず元へ戻るので、基準日の価格は常に base(型Aの条件が毎回同じ)。"""
    idx = {d: i for i, d in enumerate(DAYS)}
    p = [base] * len(DAYS)
    for a in asof_dates:
        i = idx[a]
        for j in range(1, 16):
            p[i + j] = base * (1 + 0.2 * j / 15)
    return p


def flat_prices(base: float) -> list[float]:
    return [base * (1 + 0.004 * ((i % 7) - 3) / 3) for i in range(len(DAYS))]    # ±0.4% の小さな揺れ


def world(n_a=6, n_plain=24, delisted=True):
    frames, fins, rows = {}, [], []
    for i in range(n_a):
        code = f"A{i:03d}"
        frames[code] = frame(DAYS, wave_prices(350.0, TEST_DATES))
        fins.append(fin_row(code, period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14)))
        rows.append(universe_row(code, sector="機械"))
    for i in range(n_plain):
        code = f"P{i:03d}"
        frames[code] = frame(DAYS, flat_prices(350.0))
        fins.append(plain_fin(code, period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14)))
        rows.append(universe_row(code, sector="機械"))
    if delisted:
        rows.append(universe_row("X999", delisted=date(2022, 1, 5)))      # 上場廃止の銘柄を1つ入れておく
    return Universe(rows), store(*fins), FramePriceSource(frames)


def test_month_end_dates_are_last_business_days():
    ds = bt.month_end_dates(date(2026, 1, 1), date(2026, 3, 31))
    assert ds == [date(2026, 1, 30), date(2026, 2, 27), date(2026, 3, 31)]
    assert all(is_business_day(d) for d in ds)


def test_wilson_interval_basics():
    lo, hi = bt.wilson(50, 100)
    assert lo < 0.5 < hi and 0.39 < lo and hi < 0.61
    assert bt.wilson(0, 0)[0] != bt.wilson(0, 0)[0]            # nan


def test_detects_planted_effect_in_type_a():
    uni, fin, px = world()
    res = bt.run_backtest(uni, fin, px, TOPIX, TEST_DATES, TH)
    assert res.warnings == []
    s = res.summary.set_index("group")
    assert s.loc["A", "moved_rate"] == pytest.approx(1.0)
    assert s.loc["ベースライン(全体)", "moved_rate"] == pytest.approx(6 / 30)
    assert s.loc["A", "n"] >= TH.min_samples
    assert s.loc["A", "p_value"] < 0.05
    assert s.loc["A", "verdict"].startswith("ベースラインより高い")
    assert s.loc["B", "n"] == 0 and s.loc["B", "verdict"] == "該当なし"


def test_no_effect_world_is_not_reported_as_a_win():
    uni, fin, px = world()
    frames = {c: frame(DAYS, flat_prices(350.0)) for c in px.frames}              # 型Aの銘柄も動かさない
    res = bt.run_backtest(uni, fin, FramePriceSource(frames), TOPIX, TEST_DATES, TH)
    assert res.summary.set_index("group").loc["A", "verdict"] == "ベースラインと差があるとは言えない"


def test_small_sample_gets_no_conclusion():
    uni, fin, px = world()
    res = bt.run_backtest(uni, fin, px, TOPIX, TEST_DATES[:3], TH)                 # 3日×6銘柄=18件
    assert res.summary.set_index("group").loc["A", "verdict"].startswith("サンプル不足")


def test_survivorship_warning_and_require_delisted():
    uni, fin, px = world(delisted=False)
    res = bt.run_backtest(uni, fin, px, TOPIX, TEST_DATES[:2], TH)
    assert any("生存者バイアス" in w for w in res.warnings)
    with pytest.raises(ValueError, match="生存者バイアス"):
        bt.run_backtest(uni, fin, px, TOPIX, TEST_DATES[:2], TH, require_delisted=True)


def test_lookahead_trap_financials_disclosed_later_are_not_used():
    """開示日 D の財務は、基準日 D 以前には使わない(当日も使わない)。D の翌営業日から使う。"""
    d_disc = TEST_DATES[5]
    uni, fin, px = world(n_a=0, n_plain=0)
    trap = Universe([universe_row("T001")])
    fins = store(plain_fin("T001", period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14)),
                 fin_row("T001", period_end=date(2026, 3, 31), disclosed=d_disc))      # 後から出る「型Aになる」財務
    frames = FramePriceSource({"T001": frame(DAYS, flat_prices(350.0))})
    dates = [TEST_DATES[4], d_disc, add_business_days(d_disc, 1), TEST_DATES[7]]
    res = bt.run_backtest(trap, fins, frames, TOPIX, dates, TH)
    got = {r.asof: r.hit_A for r in res.obs.itertuples()}
    assert got[TEST_DATES[4]] is False
    assert got[d_disc] is False                                   # 開示当日は使わない
    assert got[add_business_days(d_disc, 1)] is True
    assert got[TEST_DATES[7]] is True


def test_delisted_stock_is_included_until_delisting_and_truncated_flag_is_set():
    cut = DAYS.index(TEST_DATES[5]) + 6                         # 基準日の6営業日後で株価が途切れる
    short = frame(DAYS[:cut], wave_prices(350.0, [])[:cut])
    uni = Universe([universe_row("D001", delisted=DAYS[cut])])
    fins = store(fin_row("D001", period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14)))
    res = bt.run_backtest(uni, fins, FramePriceSource({"D001": short}), TOPIX, [TEST_DATES[5]], TH)
    assert len(res.obs) == 1 and bool(res.obs.iloc[0]["truncated"]) is True
    assert any("途切れた" in w for w in res.warnings)
    assert uni.members(DAYS[cut]) == []                         # 上場廃止後は母集団から外れる


def test_stock_without_any_forward_price_is_counted_and_warned():
    cut = DAYS.index(TEST_DATES[5]) + 1                         # 基準日の翌日に株価が無い
    short = frame(DAYS[:cut], [350.0] * cut)
    uni = Universe([universe_row("D002", delisted=DAYS[cut])])
    fins = store(fin_row("D002", period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14)))
    res = bt.run_backtest(uni, fins, FramePriceSource({"D002": short}), TOPIX, [TEST_DATES[5]], TH)
    assert res.obs.empty and any("観測が1件もありません" in w for w in res.warnings)
    assert any("外した観測" in w for w in res.warnings)


def test_illiquid_stocks_are_excluded_from_both_groups():
    uni, fin, px = world(n_a=2, n_plain=2)
    frames = dict(px.frames)
    frames["A000"] = frame(DAYS, wave_prices(350.0, TEST_DATES), 100.0)       # 売買代金が下限未満
    res = bt.run_backtest(uni, fin, FramePriceSource(frames), TOPIX, TEST_DATES[:4], TH)
    assert "A000" not in set(res.obs["code"])


def test_report_markdown_contains_warnings_and_table():
    uni, fin, px = world(delisted=False)
    res = bt.run_backtest(uni, fin, px, TOPIX, TEST_DATES, TH)
    md = bt.render_markdown(res, TH, START, END)
    assert "⚠ 銘柄マスタに上場廃止" in md and "| A |" in md and "別の期間" in md


# ---- 検定の作りと、期間別の比較 ----

def _obs(rows):
    return pd.DataFrame(rows)


def test_stratified_permutation_ignores_regime_clustering_but_naive_does_not():
    """該当が「全銘柄が動きやすい日」に集中しているだけで、同じ日の中では優位性がない世界。
    日付をまたいで混ぜる検定は有意と誤判定し、日付ごとに層別した検定は有意としない。"""
    rows = []
    for d in range(10):
        hot = d < 2                                    # 2日だけ全銘柄が動きやすい日(地合いが良い)
        m, k = 50, (40 if hot else 5)                  # その日のベースライン:50銘柄中、動いた銘柄数
        n_sel = 20 if hot else 1                       # 該当は、動きやすい日に集中
        sel_moved = round(n_sel * k / m)               # 同じ日の中では、該当の動いた割合 = ベースラインの割合
        for i in range(m):
            moved = i < k
            selected = (moved and i < sel_moved) or (not moved and (i - k) < n_sel - sel_moved)
            rows.append({"asof": d, "moved": moved, "sel": selected})
    obs = _obs(rows)
    assert int(obs["sel"].sum()) == 2 * 20 + 8 * 1
    p_strat = bt.perm_p_value(obs, obs["sel"], 2000, 1, stratified=True)
    p_naive = bt.perm_p_value(obs, obs["sel"], 2000, 1, stratified=False)
    assert p_naive < 0.01                              # 誤って「有意」
    assert p_strat > 0.2                               # 層別すれば有意にならない


def test_stratified_permutation_still_detects_real_within_date_edge():
    rows = []
    for d in range(30):
        for i in range(50):
            rows.append({"asof": d, "sel": i < 4, "moved": i < 4 or 10 <= i < 15})   # 該当4銘柄は必ず動く。他は10%
    obs = _obs(rows)
    assert obs["sel"].sum() == 120 and obs.loc[obs["sel"], "moved"].all()
    assert bt.perm_p_value(obs, obs["sel"], 2000, 1) < 0.01


def test_date_paired_lift_basics():
    rows = []
    for d in range(30):
        for i in range(20):
            rows.append({"asof": d, "sel": i < 3, "x": (0.05 if i < 3 else 0.0) + (0.001 * ((d * 7 + i) % 5))})
    obs = _obs(rows)
    r = bt.date_paired_lift(obs, obs["sel"], "x")
    assert r["n_dates"] == 30 and r["lift"] > 0.04 and r["t"] > 5 and r["pos_share"] == 1.0
    flat = obs.assign(x=[0.001 * ((i * 13) % 7) for i in range(len(obs))])
    assert abs(bt.date_paired_lift(flat, flat["sel"], "x")["t"]) < 3
    assert bt.date_paired_lift(obs.iloc[:20], obs.iloc[:20]["sel"], "x")["n_dates"] == 1


def test_horizon_table_verdicts():
    th = ValueThresholds(horizons=(20, 120), min_dates=24)
    rows = []
    for d in range(30):
        for i in range(20):
            a = i < 3
            rows.append({"asof": d, "hit_A": a, "hit_B": False, "hit_C": False, "hit_D": False, "n_types": int(a),
                         "excess_final_20": 0.001 * ((d + i) % 3),                         # 20日では差なし
                         "excess_final_120": (0.08 if a else 0.0) + 0.002 * ((d * 3 + i) % 4)})   # 120日では差あり
    t = bt.horizon_table(_obs(rows), th).set_index(["group", "horizon"])
    assert t.loc[("A", 120), "verdict"].startswith("ベースラインより高い")
    assert t.loc[("A", 20), "verdict"].startswith("ベースラインと差があるとは言えない")
    assert t.loc[("B", 120), "verdict"] == "該当なし"
    few = bt.horizon_table(_obs(rows[: 20 * 10]), th).set_index(["group", "horizon"])
    assert few.loc[("A", 120), "verdict"].startswith("基準日数不足")


def test_long_horizons_are_unavailable_near_the_end_of_data():
    uni, fin, px = world(n_a=2, n_plain=2)
    res = bt.run_backtest(uni, fin, px, TOPIX, [TEST_DATES[0], TEST_DATES[-1]], ValueThresholds(permutation_draws=100))
    first = res.obs[res.obs["asof"] == TEST_DATES[0]]
    last = res.obs[res.obs["asof"] == TEST_DATES[-1]]
    assert first["excess_final_250"].notna().all()                    # 十分に先まで株価がある
    assert last["excess_final_20"].notna().all() and last["excess_final_60"].notna().all()
    assert last["excess_final_250"].isna().all()                      # データの終わりを超える期間は未確定
    assert not res.horizons.empty


def test_missing_prices_without_delisting_make_the_horizon_unavailable_not_truncated():
    cut = DAYS.index(TEST_DATES[5]) + 30                              # 基準日の30営業日後で株価が途切れる(上場廃止ではない)
    short = frame(DAYS[:cut], [350.0] * cut)
    uni = Universe([universe_row("G001")])                            # delisted なし
    fins = store(fin_row("G001", period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14)))
    res = bt.run_backtest(uni, fins, FramePriceSource({"G001": short}), TOPIX, [TEST_DATES[5]],
                          ValueThresholds(permutation_draws=100))
    row = res.obs.iloc[0]
    assert row["excess_final_20"] == row["excess_final_20"]           # 20日はデータがある
    assert row["excess_final_60"] != row["excess_final_60"]           # 60日は欠け=未確定(NaN)。最後の終値で埋めない


def test_report_has_horizon_table():
    uni, fin, px = world()
    res = bt.run_backtest(uni, fin, px, TOPIX, TEST_DATES, ValueThresholds(permutation_draws=100))
    md = bt.render_markdown(res, ValueThresholds(), START, END)
    assert "期間別(日付ごとの比較" in md and "検出力の目安" in md


# ---- 株価だけの過去検証(財務データなし) ----

def dip_prices(base: float, asof_dates: list[date]) -> list[float]:
    """各基準日にだけ -30% へ急落し、その後15営業日で元の水準へ戻る(前回の急落が25日線に残っても、乖離が閾値を超える深さ)(「売られすぎ→反発」を仕込んだ世界)。"""
    idx = {d: i for i, d in enumerate(DAYS)}
    p = [base] * len(DAYS)
    for a in asof_dates:
        i = idx[a]
        p[i] = base * 0.70
        for j in range(1, 16):
            p[i + j] = base * (0.70 + 0.30 * j / 15)
    return p


def price_world(n_dip=6, n_plain=24):
    frames, rows = {}, []
    for i in range(n_dip):
        frames[f"R{i:03d}"] = frame(DAYS, dip_prices(350.0, TEST_DATES))
        rows.append(universe_row(f"R{i:03d}", sector="機械"))
    for i in range(n_plain):
        frames[f"P{i:03d}"] = frame(DAYS, flat_prices(350.0))
        rows.append(universe_row(f"P{i:03d}", sector="機械"))
    rows.append(universe_row("X999", delisted=date(2022, 1, 5)))
    return Universe(rows), FramePriceSource(frames)


def test_price_backtest_detects_planted_oversold_rebound():
    uni, px = price_world()
    res = bt.run_price_backtest(uni, px, TOPIX, TEST_DATES, TH)
    assert res.warnings == []
    d = res.obs[res.obs["hit_D"]]
    assert set(d["code"]) == {f"R{i:03d}" for i in range(6)} and d["moved"].all()
    assert {"bars", "drawdown_5y", "ret_20", "ret_60", "vol_surge", "vol_60", "deviation"} <= set(res.obs.columns)
    s = res.summary.set_index("group")
    assert s.loc["D", "verdict"].startswith("ベースラインより高い")
    assert s.loc["A", "verdict"] == "該当なし"                                  # 財務が要る型は該当しない
    h = res.horizons.set_index(["group", "horizon"])
    assert h.loc[("D", 20), "lift"] > 0.05 and h.loc[("D", 20), "t"] > 2


def test_price_backtest_filters_and_survivorship_warning():
    uni, px = price_world(n_dip=2, n_plain=2)
    uni = Universe([r for r in uni.rows if r.code != "X999"])
    res = bt.run_price_backtest(uni, px, TOPIX, TEST_DATES[:6], TH, size_ok=lambda c: c != "R000")
    assert "R000" not in set(res.obs["code"])                                   # 規模の絞り込み
    assert any("生存者バイアス" in w and "型D" in w for w in res.warnings)
    with pytest.raises(ValueError):
        bt.run_price_backtest(uni, px, TOPIX, TEST_DATES[:6], TH, require_delisted=True)


def test_full_backtest_records_selection_features_even_when_some_fields_are_missing():
    """選定ルールの検証用の特徴(営業利益・希薄化・出来高の兆しなど)を残す。現預金や有利子負債が欠けた銘柄でも落ちない。"""
    uni = Universe([universe_row("M001"), universe_row("M002")])
    fins = store(fin_row("M001", period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14)),
                 fin_row("M002", period_end=date(2021, 3, 31), disclosed=date(2021, 5, 14), cash=None, interest_debt=None))
    frames = FramePriceSource({c: frame(DAYS, flat_prices(350.0)) for c in ("M001", "M002")})
    res = bt.run_backtest(uni, fins, frames, TOPIX, TEST_DATES[:3], ValueThresholds(permutation_draws=50))
    assert {"oi_ttm", "ni_ttm", "oi_yoy", "dilution", "cash_over_debt", "vol_surge", "ret_60", "deviation"} <= set(res.obs.columns)
    assert set(res.obs["code"]) <= {"M001", "M002"}
