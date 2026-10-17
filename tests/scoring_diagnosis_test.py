from __future__ import annotations

import pytest

from assoc.config import Thresholds
from assoc.scoring.diagnosis import DiagnosisInput, diagnose

THRESHOLDS = Thresholds()


def test_label_priced_in_progress():
    inp = DiagnosisInput(attention_high=True, tier1_reacted=True, candidate_reacted=True, priced_in_value=0.3)
    assert diagnose(inp, THRESHOLDS) == "織り込み進行"


def test_label_time_lag():
    inp = DiagnosisInput(attention_high=True, tier1_reacted=True, candidate_reacted=False, priced_in_value=0.0)
    assert diagnose(inp, THRESHOLDS) == "時間差あり"


def test_label_time_lag_regardless_of_attention():
    inp = DiagnosisInput(attention_high=False, tier1_reacted=True, candidate_reacted=False, priced_in_value=0.0)
    assert diagnose(inp, THRESHOLDS) == "時間差あり"


def test_label_ignored_suspicion():
    inp = DiagnosisInput(attention_high=True, tier1_reacted=False, candidate_reacted=False, priced_in_value=0.0)
    assert diagnose(inp, THRESHOLDS) == "⚠ 無視されている疑い"


def test_label_undiscovered():
    inp = DiagnosisInput(attention_high=False, tier1_reacted=False, candidate_reacted=False, priced_in_value=0.0)
    assert diagnose(inp, THRESHOLDS) == "未発見"


def test_label_early_bird_takes_priority_over_priced_in_progress():
    # 注目度が低いのに候補が反応した -> 5番目のラベルが1番目より優先される
    inp = DiagnosisInput(attention_high=False, tier1_reacted=False, candidate_reacted=True, priced_in_value=0.1)
    assert diagnose(inp, THRESHOLDS) == "早耳の先回り"


def test_label_early_bird_even_when_tier1_also_reacted():
    inp = DiagnosisInput(attention_high=False, tier1_reacted=True, candidate_reacted=True, priced_in_value=0.1)
    assert diagnose(inp, THRESHOLDS) == "早耳の先回り"


def test_candidate_reacted_fully_priced_in_still_reports_progress():
    # 表にない組み合わせ(候補は反応したが織り込み度は完了(80%)以上)。
    # 解釈:引き続き「織り込み進行」を返す(最終報告に記載)。
    inp = DiagnosisInput(attention_high=True, tier1_reacted=True, candidate_reacted=True, priced_in_value=0.9)
    assert diagnose(inp, THRESHOLDS) == "織り込み進行"


def test_ignored_penalty_waits_for_grace_days():
    """開始直後は反応する時間が無いので、確率を半分にする『無視されている疑い』にしない(D41)。"""
    from datetime import date
    from assoc.pipeline.evaluate import adjust_probability
    from assoc.scoring.diagnosis import 判定前
    assert 判定前 != "⚠ 無視されている疑い"
    # 判定前のラベルでは IGNORED_FACTOR が掛からない
    class _Sc:
        verified_ratio = 1.0
        denied_arrows = 0
        strength = "Medium"
    prob, notes = adjust_probability(0.4, _Sc(), {"direction_clear": True}, 判定前, None, "Medium")
    assert not any("無視" in n for n in notes)
    prob2, _ = adjust_probability(0.4, _Sc(), {"direction_clear": True}, "⚠ 無視されている疑い", None, "Medium")
    assert prob2 == pytest.approx(prob * 0.5)
