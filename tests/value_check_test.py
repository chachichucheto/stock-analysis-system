from datetime import date

from assoc.market.prices import FramePriceSource
from assoc.value.check import check_data
from assoc.value.data import Universe
from value_helpers import ASOF, days_ending, fin_row, frame, store, universe_row


def test_clean_data_has_no_errors_and_reports_info():
    u = Universe([universe_row("1000"), universe_row("2000", delisted=date(2024, 1, 5))])
    r = check_data(u, store(fin_row("1000"), fin_row("2000")))
    assert r.errors == [] and any("銘柄マスタ 2銘柄" in i for i in r.info)


def test_warns_without_delisted_and_without_financials():
    u = Universe([universe_row("1000"), universe_row("2000")])
    r = check_data(u, store(fin_row("1000")))
    assert any("生存者バイアス" in w for w in r.warnings)
    assert any("財務データが1行も無い" in w for w in r.warnings)


def test_errors_for_lookahead_dates_duplicates_and_shares():
    u = Universe([universe_row("1000"), universe_row("1000")])
    bad = fin_row("1000", period_end=date(2026, 3, 31), disclosed=date(2026, 3, 1), shares_ex_treasury=0.0)
    r = check_data(u, store(bad))
    joined = "\n".join(r.errors)
    assert "重複" in joined and "先読み" in joined and "発行済株式数" in joined


def test_price_coverage_warning():
    u = Universe([universe_row("1000"), universe_row("2000")])
    px = FramePriceSource({"1000": frame(days_ending(ASOF, 5), [350.0] * 5)})
    r = check_data(u, store(fin_row("1000"), fin_row("2000")), px, ASOF)
    assert any("1/2" in i for i in r.info) and any("株価DBの欠け" in w for w in r.warnings)
