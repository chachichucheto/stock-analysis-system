from __future__ import annotations

from datetime import datetime, timezone

from assoc.events.cluster import ClusterInput, char_bigrams, cluster, extract_entities, jaccard


def _ci(id_, title, kind="news", source="a", code=None, hour=0):
    return ClusterInput(
        id=id_, title=title, kind=kind, source=source,
        first_observed_at=datetime(2026, 9, 25, hour, tzinfo=timezone.utc), code=code,
    )


def test_char_bigrams_and_jaccard_basic():
    a = char_bigrams("半導体メーカーが増産")
    b = char_bigrams("半導体メーカーが増産へ")
    assert jaccard(a, b) > 0.8
    assert jaccard(set(), set()) == 0.0


def test_extract_entities_picks_kanji_and_katakana_runs():
    entities = extract_entities("大手半導体メーカーのサンプル物産が投資を発表")
    assert "半導体" in entities or "大手半導体" in "".join(entities)
    assert "サンプル物産" in entities or "サンプル" in entities


def test_cluster_groups_similar_headlines_into_one_event():
    items = [
        _ci("n1", "大手半導体メーカーが国内工場に新規投資を発表", hour=0),
        _ci("n2", "大手半導体メーカーが国内工場へ新規投資、正式発表", hour=1),
        _ci("n3", "台風が接近 西日本で大雨のおそれ", hour=2),
    ]
    events = cluster(items, threshold=0.5)
    assert len(events) == 2
    sizes = sorted(len(e.news_ids) for e in events)
    assert sizes == [1, 2]


def test_cluster_uses_earliest_first_observed_at():
    items = [
        _ci("n1", "サンプル電機が業績予想を上方修正", hour=5),
        _ci("n2", "サンプル電機、業績予想を上方修正すると発表", hour=2),
    ]
    events = cluster(items, threshold=0.5)
    assert len(events) == 1
    assert events[0].first_observed_at.hour == 2


def test_cluster_disclosure_marks_disclosure_type_and_codes():
    items = [
        _ci("d1", "大量保有報告書の提出に関するお知らせ", kind="disclosure", source="tdnet", code="1301"),
    ]
    events = cluster(items)
    assert events[0].disclosure_type == "disclosure"
    assert events[0].codes == ["1301"]
    assert events[0].news_ids == []  # 開示のみなので news_ids は空


def test_cluster_same_headline_twice_has_same_novelty_hash():
    items1 = [_ci("n1", "サンプルHDが自己株式取得を発表")]
    items2 = [_ci("n1b", "サンプルHDが自己株式取得を発表")]
    e1 = cluster(items1)[0]
    e2 = cluster(items2)[0]
    assert e1.novelty_hash == e2.novelty_hash


def test_cluster_different_headline_has_different_novelty_hash():
    e1 = cluster([_ci("n1", "サンプルHDが自己株式取得を発表")])[0]
    e2 = cluster([_ci("n2", "台風が接近 西日本で大雨のおそれ")])[0]
    assert e1.novelty_hash != e2.novelty_hash
