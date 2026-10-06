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
