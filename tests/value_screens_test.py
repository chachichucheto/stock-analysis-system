from datetime import date

import pytest

from assoc.value.dangers import detect_dangers
from assoc.value.screens import run_screens, screen_a, screen_b, screen_c, screen_d
from assoc.value.snapshot import build_snapshot, passes_base
from assoc.value.thresholds import ValueThresholds
from value_helpers import ASOF, days_ending, fin_row, frame, plain_fin, store, universe_row

TH = ValueThresholds()


def snap(fin_rows, closes=None, volumes=200_000.0, n=120, sector="機械", code="1000"):
    closes = closes or [350.0] * n
    prices = frame(days_ending(ASOF, len(closes)), closes, volumes)
    return build_snapshot(universe_row(code, sector=sector), ASOF, store(*fin_rows), prices, TH)


def test_snapshot_is_none_without_price_on_asof():
    prices = frame(days_ending(date(2026, 10, 5), 50), [350.0] * 50)
    assert build_snapshot(universe_row(), ASOF, store(fin_row()), prices, TH) is None


def test_snapshot_ignores_prices_after_asof():
    d = days_ending(date(2026, 10, 9), 50)                      # asof より後の日を含む
    s = build_snapshot(universe_row(), ASOF, store(fin_row()), frame(d, [350.0] * 50), TH)
    assert s.prices["date"].max() == ASOF


def test_base_filter_reports_reasons():
    s = snap([fin_row()], volumes=1_000.0)                      # 売買代金が小さすぎる
    ok, why = passes_base(s, TH)
    assert not ok and any("売買代金" in w for w in why)
    ok, why = passes_base(snap([]), TH)
    assert not ok and any("財務データ" in w for w in why)
    assert passes_base(snap([fin_row()]), TH)[0] is True


def test_a_hits_via_netnet_and_reports_numbers():
    r = screen_a(snap([fin_row()]), TH)
    assert r.hit and r.facts["by"] == "netnet"
    assert r.facts["netnet"] == pytest.approx(3.5e9 / 6e9)
    assert any("ネットネット指数 0.58" in x for x in r.reasons)


def test_a_hits_via_pbr_and_equity_ratio_when_netnet_fails():
    f = fin_row(cash=1e9, receivables=1e9, investment_securities=0.0, total_liabilities=5e9,
                equity=8e9, total_assets=11e9)                   # ネットネットは不成立、PBR 0.44・自己資本比率 73%
    r = screen_a(snap([f]), TH)
    assert r.hit and r.facts["by"] == "pbr"


def test_a_misses_when_not_cheap():
    assert screen_a(snap([plain_fin("1000")]), TH).hit is False


def test_a_missing_data_never_passes():
    r = screen_a(snap([fin_row(cash=None, receivables=None, equity=None)]), TH)
    assert r.hit is False and r.missing


def _fy(year, oi, **kw):
    return fin_row(period="FY", period_end=date(year, 3, 31), disclosed=date(year, 5, 15), operating_income_ttm=oi, **kw)


def test_b_hits_for_cyclical_bottom():
    hist = [_fy(2026, -3e8), _fy(2025, -2e8), _fy(2024, 4e8), _fy(2023, 6e8), _fy(2022, 5e8)]
    closes = [900.0] * 100 + [300.0] * 100                       # ピークから -67%
    r = screen_b(snap(hist, closes=closes, n=200), TH)
    assert r.hit, r.reasons
    assert r.facts["consecutive_loss_years"] == 2


@pytest.mark.parametrize("mutate,why", [
    (lambda h: h[:3], "履歴"),                                    # 履歴が足りない
    (lambda h: [_fy(2026, 3e8)] + h[1:], "赤字"),                  # いま黒字
])
def test_b_rejects(mutate, why):
    hist = [_fy(2026, -3e8), _fy(2025, -2e8), _fy(2024, 4e8), _fy(2023, 6e8), _fy(2022, 5e8)]
    closes = [900.0] * 100 + [300.0] * 100
    assert screen_b(snap(mutate(hist), closes=closes, n=200), TH).hit is False


def test_b_rejects_when_price_not_deep_or_not_survivable():
    hist = [_fy(2026, -3e8), _fy(2025, -2e8), _fy(2024, 4e8), _fy(2023, 6e8), _fy(2022, 5e8)]
    shallow = [900.0] * 100 + [700.0] * 100
    assert screen_b(snap(hist, closes=shallow, n=200), TH).hit is False
    weak = [_fy(2026, -3e8, equity=2e9, total_assets=11e9)] + hist[1:]      # 自己資本比率 18%
    deep = [900.0] * 100 + [300.0] * 100
    assert screen_b(snap(weak, closes=deep, n=200), TH).hit is False


def _growth_rows(latest_oi=0.8e9, prev_oi=0.62e9):
    return [fin_row(period="Q3", period_end=date(2026, 6, 30), disclosed=date(2026, 8, 10), operating_income_ttm=latest_oi),
            fin_row(period="Q2", period_end=date(2026, 3, 31), disclosed=date(2026, 5, 10), operating_income_ttm=prev_oi),
            fin_row(period="Q3", period_end=date(2025, 6, 30), disclosed=date(2025, 8, 10), operating_income_ttm=0.5e9),
            fin_row(period="Q2", period_end=date(2025, 3, 31), disclosed=date(2025, 5, 10), operating_income_ttm=0.5e9)]


def _surge_volumes(n=120):
    v = [100_000.0] * n
    v[-3] = 400_000.0
    return v


def test_c_hits_for_accelerating_small_growth_with_volume_surge():
    r = screen_c(snap(_growth_rows(), volumes=_surge_volumes()), TH)
    assert r.hit, r.reasons
    assert r.facts["growth_latest"] == pytest.approx(0.6) and r.facts["growth_prev"] == pytest.approx(0.24)


def test_c_rejects_without_acceleration_or_surge_or_after_run_up():
    assert screen_c(snap(_growth_rows(prev_oi=0.9e9), volumes=_surge_volumes()), TH).hit is False   # 減速
    assert screen_c(snap(_growth_rows(), volumes=100_000.0), TH).hit is False                       # 出来高なし
    runup = [200.0] * 60 + [350.0] * 60                                                             # 60日で+75%
    assert screen_c(snap(_growth_rows(), closes=runup, volumes=_surge_volumes()), TH).hit is False


def test_c_missing_history_is_not_a_hit_and_says_why():
    r = screen_c(snap([fin_row()], volumes=_surge_volumes()), TH)
    assert r.hit is False and r.missing


def test_d_uses_sector_threshold():
    closes = [100.0] * 95 + [84.0] * 5                           # 25日線から約 -13%
    assert screen_d(snap([fin_row()], closes=closes, sector="食料品"), TH).hit is True    # 閾値 -12%
    assert screen_d(snap([fin_row()], closes=closes, sector="電気機器"), TH).hit is False  # 閾値 -20%


def test_run_screens_returns_all_four():
    assert set(run_screens(snap([fin_row()]), TH)) == {"A", "B", "C", "D"}


def test_dangers_block_and_warn():
    s = snap([fin_row(equity=-1e9)])
    assert any(d.severity == "block" for d in detect_dangers(s, TH))
    master = universe_row(monitoring=True)
    assert any("監理" in d.text for d in detect_dangers(snap([fin_row()]), TH, master=master))
    dil = [fin_row(period_end=date(2026, 3, 31), shares_ex_treasury=14e6),
           fin_row(period_end=date(2025, 3, 31), disclosed=date(2025, 5, 15), shares_ex_treasury=10e6)]
    assert any(d.severity == "block" and "希薄化" in d.text for d in detect_dangers(snap(dil), TH))
    dil_small = [fin_row(period_end=date(2026, 3, 31), shares_ex_treasury=11.5e6),
                 fin_row(period_end=date(2025, 3, 31), disclosed=date(2025, 5, 15), shares_ex_treasury=10e6)]
    assert [d.severity for d in detect_dangers(snap(dil_small), TH)] == ["warn"]
    surged = [200.0] * 100 + [400.0] * 20
    assert any("急騰" in d.text for d in detect_dangers(snap([fin_row()], closes=surged), TH))
    assert detect_dangers(snap([fin_row()]), TH) == []
