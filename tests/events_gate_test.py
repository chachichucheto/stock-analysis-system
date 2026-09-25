from __future__ import annotations

from assoc.config import Thresholds
from assoc.events.gate import GateInput, composite_attention, gate_events, is_strong_disclosure, percentile_rank


def test_percentile_rank_basic():
    history = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile_rank(11, history) == 1.0
    assert percentile_rank(10, history) == 0.9      # 「より小さい」ものの割合
    assert percentile_rank(0, history) == 0.0
    assert percentile_rank(5, history) == 0.4
    assert percentile_rank(1, [1] * 10) == 0.0      # 同じ値ばかりでも上位扱いにしない


def test_percentile_rank_empty_history_is_zero():
    assert percentile_rank(100, []) == 0.0


def test_is_strong_disclosure_matches_keywords():
    assert is_strong_disclosure("大量保有報告書の提出に関するお知らせ")
    assert is_strong_disclosure("業績予想の修正に関するお知らせ")
    assert not is_strong_disclosure("定款の一部変更に関するお知らせ")


def _history(n=100):
    return [float(i) for i in range(n)]


def test_gate_passes_on_high_attention():
    thresholds = Thresholds()
    hist = _history()
    event = GateInput(event_id="e1", title="なんでもない見出し", novelty_hash="h1",
                       media_count=99, gdelt=99, wiki=99)
    results = gate_events([event], thresholds, media_history=hist, gdelt_history=hist, wiki_history=hist)
    assert results[0].passed is True
    assert "注目度が上位5%" in results[0].reason


def test_gate_fails_when_below_all_thresholds():
    thresholds = Thresholds()
    hist = _history()
    event = GateInput(event_id="e1", title="なんでもない見出し", novelty_hash="h1",
                       media_count=1, gdelt=1, wiki=1, growth=0.0)
    results = gate_events([event], thresholds, media_history=hist, gdelt_history=hist, wiki_history=hist,
                           growth_history=hist)
    assert results[0].passed is False


def test_gate_passes_on_strong_disclosure_even_with_low_attention():
    thresholds = Thresholds()
    event = GateInput(event_id="e1", title="大量保有報告書の提出に関するお知らせ", novelty_hash="h1",
                       media_count=0, gdelt=0, wiki=0)
    results = gate_events([event], thresholds)
    assert results[0].passed is True
    assert "強い種類の開示" in results[0].reason


def test_gate_drops_known_novelty_hash_even_if_otherwise_passes():
    thresholds = Thresholds()
    event = GateInput(event_id="e1", title="大量保有報告書の提出に関するお知らせ", novelty_hash="known")
    results = gate_events([event], thresholds, seen_novelty_hashes={"known"})
    assert results[0].passed is False
    assert "新規性なし" in results[0].reason


def test_gate_enforces_max_events_limit_by_score_order():
    thresholds = Thresholds()  # gate_max_events == 3
    hist = _history()
    events = [
        GateInput(event_id=f"e{i}", title=f"開示{i}", novelty_hash=f"h{i}",
                   media_count=96 + i, gdelt=96 + i, wiki=96 + i)
        for i in range(5)
    ]
    results = gate_events(events, thresholds, media_history=hist, gdelt_history=hist, wiki_history=hist)
    passed = [r for r in results if r.passed]
    assert len(passed) == thresholds.gate_max_events
    # 一番スコアの高い e4 が通り、一番低い e0 は上限超過で落ちる
    by_id = {r.event_id: r for r in results}
    assert by_id["e4"].passed is True
    assert by_id["e0"].passed is False
    assert "上限" in by_id["e0"].reason


def test_gate_forced_in_bypasses_max_events_limit():
    thresholds = Thresholds()
    hist = _history()
    events = [
        GateInput(event_id=f"e{i}", title=f"開示{i}", novelty_hash=f"h{i}",
                   media_count=90 + i, gdelt=90 + i, wiki=90 + i)
        for i in range(3)
    ] + [
        GateInput(event_id="scenario_related", title="進行中シナリオ関連のニュース",
                   novelty_hash="h_scenario", media_count=1, gdelt=1, wiki=1, codes=["1301"]),
    ]
    # scenario_related は注目度が足りないが、進行中シナリオの銘柄コードに一致するので、
    # 別枠で通す(小さな続報でもシナリオの強化・弱体化の材料になるため。DESIGN §6.2-4)。
    results = gate_events(
        events, thresholds, media_history=hist, gdelt_history=hist, wiki_history=hist,
        active_codes={"1301"},
    )
    by_id = {r.event_id: r for r in results}
    assert by_id["scenario_related"].passed is True
    assert by_id["scenario_related"].forced_in is True
    assert by_id["scenario_related"].reason == "進行中シナリオに関係"


def test_gate_forced_in_added_beyond_limit_when_conditions_met():
    thresholds = Thresholds()
    hist = _history()
    events = [
        GateInput(event_id=f"e{i}", title=f"開示{i}", novelty_hash=f"h{i}",
                   media_count=97 + i, gdelt=97 + i, wiki=97 + i)
        for i in range(3)
    ] + [
        GateInput(event_id="scenario_related", title="進行中シナリオ関連の大量保有報告書",
                   novelty_hash="h_scenario", media_count=1, gdelt=1, wiki=1, codes=["1301"]),
    ]
    results = gate_events(
        events, thresholds, media_history=hist, gdelt_history=hist, wiki_history=hist,
        active_codes={"1301"},
    )
    by_id = {r.event_id: r for r in results}
    passed = [r for r in results if r.passed]
    assert len(passed) == thresholds.gate_max_events + 1  # 別枠1件が上限の枠外で追加
    assert by_id["scenario_related"].passed is True
    assert by_id["scenario_related"].forced_in is True


def test_composite_attention_uses_percentile_of_each_metric():
    hist = _history()
    event = GateInput(event_id="e1", title="x", novelty_hash="h1", media_count=99, gdelt=0, wiki=0)
    score = composite_attention(event, media_history=hist, gdelt_history=hist, wiki_history=hist)
    assert 0.3 < score < 0.4  # media は上位、gdelt/wiki は最下位なので平均は中庸より低い


def test_cold_start_passes_top_events_by_media_count():
    from assoc.config import Thresholds
    from assoc.events.gate import GateInput, gate_events
    events = [GateInput(event_id=f"e{i}", title=f"見出し{i}", novelty_hash=f"h{i}", media_count=float(i))
              for i in range(1, 6)]
    results = gate_events(events, Thresholds(), media_history=[1.0] * 5)   # 履歴が少ない
    passed = [r.event_id for r in results if r.passed]
    assert passed == ["e3", "e4", "e5"]            # 媒体数の多い順に上限3件


def test_unmeasured_indicators_do_not_inflate_score():
    from assoc.events.gate import GateInput, composite_attention
    e = GateInput(event_id="x", title="t", novelty_hash="h", media_count=1.0)
    score = composite_attention(e, media_history=[5.0] * 30, gdelt_history=[0.0] * 30, wiki_history=[0.0] * 30)
    assert score == 0.0
