import pytest

from assoc.value.models import Danger, ScreenResult
from assoc.value.risk import portfolio_check, position_size, stop_price
from assoc.value.score import Catalyst, count_types, evaluate_candidate, prelist_for_llm, rank
from assoc.value.thresholds import ValueThresholds, thresholds_from_config
from value_helpers import ASOF, days_ending, fin_row, frame, store, universe_row
from assoc.value.snapshot import build_snapshot

TH = ValueThresholds()


def screens(**hits):
    return {k: ScreenResult(k, hits.get(k, False)) for k in "ABCD"}


def snap(code="1000"):
    return build_snapshot(universe_row(code), ASOF, store(fin_row(code)), frame(days_ending(ASOF, 120), [350.0] * 120), TH)


def cand(code="1000", dangers=None, catalyst=None, **hits):
    return evaluate_candidate(snap(code), screens(**hits), dangers or [], catalyst, TH)


def test_d_counts_only_when_overlapping_with_a_to_c():
    assert count_types(screens(D=True)) == []
    assert count_types(screens(A=True)) == ["A"]
    assert count_types(screens(A=True, D=True)) == ["A", "D"]


def test_qualifies_by_two_types_or_one_type_plus_catalyst():
    assert cand(A=True, B=True).qualifies
    assert not cand(A=True).qualifies
    assert cand(A=True, catalyst=Catalyst(types=["株主還元"], strength="Medium")).qualifies
    assert not cand(A=True, catalyst=Catalyst(types=["株主還元"], strength="Weak")).qualifies   # 0.5点では足りない
    assert not cand(D=True, catalyst=Catalyst(types=["業績"], strength="Strong")).qualifies      # D 単独は候補にならない


def test_block_excludes_and_warn_reduces_score():
    blocked = cand(A=True, B=True, dangers=[Danger("block", "債務超過")])
    assert blocked.blocked and not blocked.qualifies
    plain, warned = cand(A=True, B=True), cand(A=True, B=True, dangers=[Danger("warn", "希薄化")])
    assert warned.score == pytest.approx(plain.score - TH.warn_penalty)


def test_rank_is_deterministic_and_filters():
    c1 = cand("2000", A=True, B=True)
    c2 = cand("1000", A=True, B=True)
    c3 = cand("3000", A=True, B=True, C=True)
    c4 = cand("4000", A=True)
    assert [c.code for c in rank([c1, c2, c3, c4])] == ["3000", "1000", "2000"]
    assert [c.code for c in rank([c1, c2, c3], limit=1)] == ["3000"]


def test_prelist_excludes_blocked_and_d_only_and_respects_limit():
    cs = [cand("1000", A=True), cand("2000", D=True), cand("3000", A=True, dangers=[Danger("block", "x")]),
          cand("4000", B=True, C=True)]
    assert [c.code for c in prelist_for_llm(cs, TH)] == ["4000", "1000"]
    small = ValueThresholds(llm_max_items=1)
    assert len(prelist_for_llm(cs, small)) == 1


def test_thresholds_override_and_unknown_key():
    class Cfg:
        def __init__(self, d): self.d = d
        def section(self, name): return self.d.get(name, {})
    assert thresholds_from_config(Cfg({"value": {"thresholds": {"min_turnover_yen": 5e7}}})).min_turnover_yen == 5e7
    with pytest.raises(ValueError, match="未知のキー"):
        thresholds_from_config(Cfg({"value": {"thresholds": {"min_turnover": 5e7}}}))


def test_stop_price_is_below_recent_low_and_capped_at_entry():
    d = days_ending(ASOF, 30)
    p = frame(d, [100.0] * 29 + [95.0])
    assert stop_price(p, 95.0, TH) == pytest.approx(min(95.0 * 0.98, 95.0))


def test_position_size_limited_by_risk():
    r = position_size(10_000_000, 1000.0, 900.0, 5e9, TH)       # 損失上限 75,000円 ÷ 100円 = 750株 → 700株
    assert r.limited_by == "risk" and r.shares == 700
    assert r.risk_yen == pytest.approx(70_000.0)


def test_position_size_limited_by_liquidity_and_position_cap():
    liq = position_size(100_000_000, 1000.0, 950.0, 2e6, TH)    # 売買代金 200万円の5% = 10万円 → 100株
    assert liq.limited_by == "liquidity" and liq.shares == 100
    cap = position_size(1_000_000, 1000.0, 990.0, 5e9, TH)      # 総資金比20% = 20万円 → 200株
    assert cap.limited_by == "position_cap" and cap.shares == 200


def test_position_size_skips_wide_or_invalid_stops():
    assert position_size(1e7, 1000.0, 600.0, 5e9, TH).limited_by == "skip"       # 損切り幅 40%
    assert position_size(1e7, 1000.0, None, 5e9, TH).limited_by == "skip"
    assert position_size(1e7, 1000.0, 1000.0, 5e9, TH).limited_by == "skip"
    tiny = position_size(100_000, 5000.0, 4900.0, 5e9, TH)                          # 1単元も買えない
    assert tiny.shares == 0 and tiny.limited_by == "skip"
    with pytest.raises(ValueError):
        position_size(0, 1000.0, 900.0, 1e9, TH)


def test_portfolio_check():
    assert portfolio_check(1e7, [100_000, 200_000], TH) == []
    assert portfolio_check(1e7, [300_000, 300_000], TH)        # 60万円 > 50万円
