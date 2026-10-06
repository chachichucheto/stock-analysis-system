from datetime import date

import pytest

from assoc.value import metrics
from value_helpers import ASOF, days_ending, fin_row, frame


def test_liquid_assets_kabu1000_definition():
    f = fin_row(cash=3e9, receivables=2e9, securities=0.5e9, investment_securities=1e9, allowance=0.1e9)
    assert metrics.liquid_assets(f) == pytest.approx(3e9 + 2e9 + 0.5e9 + 1e9 - 0.1e9)


def test_liquid_assets_conservative_is_lower():
    f = fin_row()
    assert metrics.liquid_assets(f, metrics.CONSERVATIVE) < metrics.liquid_assets(f)


def test_liquid_assets_missing_required_field_is_none():
    assert metrics.liquid_assets(fin_row(cash=None)) is None
    assert metrics.liquid_assets(fin_row(receivables=None)) is None


def test_netnet_index_value_and_nonpositive_denominator():
    f = fin_row()                                   # 流動 8e9 − 負債 2e9 = 6e9
    mcap = metrics.market_cap(350.0, f)
    assert mcap == pytest.approx(3.5e9)
    assert metrics.netnet_index(mcap, f) == pytest.approx(3.5e9 / 6e9)
    bad = fin_row(cash=1e9, receivables=1e9, investment_securities=0.0, total_liabilities=5e9)
    assert metrics.netnet_index(3.5e9, bad) is None   # 分母が0以下は「該当しない」


def test_pbr_equity_ratio_per_and_none_cases():
    f = fin_row()
    assert metrics.pbr(3.5e9, f) == pytest.approx(3.5e9 / 9e9)
    assert metrics.equity_ratio(f) == pytest.approx(9e9 / 11e9)
    assert metrics.per(3.5e9, f) == pytest.approx(3.5e9 / 0.4e9)
    assert metrics.per(3.5e9, fin_row(net_income_ttm=-1e8)) is None
    assert metrics.pbr(3.5e9, fin_row(equity=-1e9)) is None


def test_yoy_requires_positive_base():
    assert metrics.yoy(6.0, 5.0) == pytest.approx(0.2)
    assert metrics.yoy(6.0, 0.0) is None
    assert metrics.yoy(6.0, -1.0) is None
    assert metrics.yoy(None, 5.0) is None


def test_dilution():
    a, b = fin_row(shares_ex_treasury=11e6), fin_row(shares_ex_treasury=10e6)
    assert metrics.dilution(a, b) == pytest.approx(0.1)
    assert metrics.dilution(a, None) is None


def test_fy_stats_counts_and_consecutive_losses():
    rows = [fin_row(operating_income_ttm=v) for v in (-1e8, -2e8, 3e8, 4e8, -5e8)]   # 新しい順
    st = metrics.fy_stats(rows)
    assert st == {"loss_years": 3, "profit_years": 2, "consecutive_loss_years": 2, "years": 5}
    assert metrics.fy_stats([fin_row(operating_income_ttm=None)]) is None


def test_ma_deviation():
    d = days_ending(ASOF, 30)
    closes = [100.0] * 24 + [80.0] * 6
    dev = metrics.ma_deviation(frame(d, closes), 25)
    assert dev == pytest.approx(80.0 / (sum(closes[-25:]) / 25) - 1)
    assert metrics.ma_deviation(frame(d[:10], closes[:10]), 25) is None


def test_volume_surge_detects_spike_within_window():
    d = days_ending(ASOF, 60)
    vols = [100_000.0] * 60
    vols[-3] = 250_000.0
    assert metrics.volume_surge(frame(d, [100.0] * 60, vols), 2.0, 10, 20) is True
    vols2 = [100_000.0] * 60
    vols2[-30] = 900_000.0                              # 窓の外の急増は数えない
    assert metrics.volume_surge(frame(d, [100.0] * 60, vols2), 2.0, 10, 20) is False
    assert metrics.volume_surge(frame(d[:20], [100.0] * 20), 2.0, 10, 20) is None


def test_drawdown_and_returns_and_turnover():
    d = days_ending(ASOF, 100)
    closes = [300.0] * 50 + [100.0] * 50
    p = frame(d, closes, 1000.0)
    assert metrics.drawdown_from_peak(p, 1250) == pytest.approx(-2 / 3)
    assert metrics.ret_over(p, 20) == pytest.approx(0.0)
    assert metrics.ret_over(p, 60) == pytest.approx(100 / 300 - 1)
    assert metrics.avg_turnover(p, 20) == pytest.approx(100.0 * 1000.0)
    assert metrics.recent_low(p, 20) == 100.0
