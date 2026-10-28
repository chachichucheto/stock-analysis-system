from datetime import date

import pandas as pd

from assoc.review import reach


def _closes():
    days = [date(2026, 10, d) for d in (1, 2, 5, 6, 7, 8, 9)]
    base = {"A": [100] * 7, "B": [100] * 7, "C": [100] * 7, "D": [100] * 7, "E": [100] * 7}
    base["B"] = [100, 100, 100, 100, 100, 100, 112]            # 5営業日後に+12%
    return pd.DataFrame(base, index=days)


def test_forward_excess_needs_enough_future_days():
    c = _closes()
    ex = reach.forward_excess(c, date(2026, 10, 2), 5)
    assert ex is not None and abs(ex["B"] - 0.12) < 1e-9 and ex["A"] == 0
    assert reach.forward_excess(c, date(2026, 10, 6), 5) is None       # 5営業日後の株価がまだ無い
    assert reach.forward_excess(c, date(2026, 10, 3), 5) is None       # 休場日は問題にしない


def test_score_case_lift_is_precision_over_base_rate():
    ex = pd.Series({"A": 0.0, "B": 0.12, "C": 0.0, "D": 0.0, "E": 0.0})
    s = reach.score_case("2026-10-01", "A", ["B", "C", "ZZ"], ex)      # ZZ は株価なし。数えない
    assert (s.related, s.hits) == (2, 1) and abs(s.base_rate - 0.2) < 1e-9
    assert abs(s.lift - 2.5) < 1e-9
    assert reach.summarize([s])["lift"] == s.lift
