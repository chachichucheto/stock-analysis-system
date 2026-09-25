from __future__ import annotations

from assoc.config import Thresholds
from assoc.scoring.picks import CandidateInput, exclusion_reason, rank_picks

THRESHOLDS = Thresholds()  # min_turnover_yen=1e8, min_price_yen=100, surged_return=0.50, max_picks=3


def _ok_candidate(**overrides) -> CandidateInput:
    base = dict(
        scenario_id="S1",
        code="1000",
        company_name="テスト株式会社",
        confidence=0.10,
        side="long",
        avg_turnover_yen=THRESHOLDS.min_turnover_yen * 2,
        price=1000.0,
        return_20d=0.05,
        evidence_all_low_tier=False,
        is_kanri_seiri=False,
    )
    base.update(overrides)
    return CandidateInput(**base)


def test_no_exclusion_for_a_clean_candidate():
    assert exclusion_reason(_ok_candidate(), THRESHOLDS) is None


def test_excludes_low_liquidity():
    c = _ok_candidate(avg_turnover_yen=THRESHOLDS.min_turnover_yen - 1)
    assert exclusion_reason(c, THRESHOLDS) is not None


def test_excludes_kanri_seiri_post():
    c = _ok_candidate(is_kanri_seiri=True)
    assert exclusion_reason(c, THRESHOLDS) == "監理・整理ポストの銘柄"


def test_excludes_price_below_100_yen():
    c = _ok_candidate(price=99.9)
    assert exclusion_reason(c, THRESHOLDS) == "株価100円未満"


def test_price_exactly_100_yen_is_not_excluded_on_that_rule():
    c = _ok_candidate(price=100.0)
    assert exclusion_reason(c, THRESHOLDS) is None


def test_excludes_already_surged_stock():
    c = _ok_candidate(return_20d=THRESHOLDS.surged_return + 0.01)
    assert exclusion_reason(c, THRESHOLDS) == "直近ですでに急騰している"


def test_surged_return_exactly_at_threshold_is_not_excluded():
    c = _ok_candidate(return_20d=THRESHOLDS.surged_return)
    assert exclusion_reason(c, THRESHOLDS) is None


def test_excludes_low_tier_only_evidence():
    c = _ok_candidate(evidence_all_low_tier=True)
    assert exclusion_reason(c, THRESHOLDS) == "裏取りの証拠が信頼度3・4の情報源だけ"


def test_caution_side_is_excluded_from_picks():
    c = _ok_candidate(side="caution")
    assert exclusion_reason(c, THRESHOLDS) == "売り方向(caution)の候補"


def test_rank_picks_orders_by_confidence_descending():
    candidates = [
        _ok_candidate(code="1000", confidence=0.05),
        _ok_candidate(code="2000", confidence=0.20),
        _ok_candidate(code="3000", confidence=0.10),
    ]
    ranked = rank_picks(candidates, THRESHOLDS)
    assert [r.candidate.code for r in ranked] == ["2000", "3000", "1000"]
    assert [r.rank for r in ranked] == [1, 2, 3]


def test_excluded_candidate_stays_in_ranking_but_is_not_a_pick():
    candidates = [
        _ok_candidate(code="1000", confidence=0.30, price=50.0),  # 除外対象だが自信度は最高
        _ok_candidate(code="2000", confidence=0.20),
        _ok_candidate(code="3000", confidence=0.10),
    ]
    ranked = rank_picks(candidates, THRESHOLDS)
    top = ranked[0]
    assert top.candidate.code == "1000"
    assert top.tier == "監視"
    assert top.excluded_reason == "株価100円未満"
    assert ranked[1].tier == "本命"


def test_max_picks_limits_the_number_of_honmei():
    candidates = [_ok_candidate(code=str(1000 + i), confidence=1.0 - i * 0.01) for i in range(5)]
    ranked = rank_picks(candidates, THRESHOLDS)
    honmei = [r for r in ranked if r.tier == "本命"]
    kanshi = [r for r in ranked if r.tier == "監視"]
    assert len(honmei) == THRESHOLDS.max_picks == 3
    assert len(kanshi) == 2
    # 監視になった2件は除外条件には当たっていない(単に max_picks 圏外)
    assert all(r.excluded_reason is None for r in kanshi)
