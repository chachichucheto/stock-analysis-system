"""辞書に無い話題を拾う「急に増えた語」(events/terms.py)と、全テーマの辞書(D39)。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from assoc.app import App
from assoc.config import Config, Thresholds
from assoc.events.burst import detect_bursts, load_triggers
from assoc.events.terms import _ngrams, detect_emerging_terms
from assoc.timeutil import JST

TH = Thresholds()
DAY = date(2026, 10, 7)


@pytest.fixture
def app(tmp_path):
    return App(Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "csv_dir"}, "collect": {}}))


_n = 0


def add(app, d: date, title: str, hour: int = 10):
    global _n
    _n += 1
    obs = datetime.combine(d, time(hour), tzinfo=JST)
    app.con.execute("INSERT INTO news_item VALUES (?, 'ITmedia NEWS', 2, ?, '', ?, 'ja', ?, ?, ?)",
                    [f"t{_n}", title, f"https://example.com/{_n}", obs, obs, f"th{_n}"])


def quiet_history(app, days=6, per_day=100):
    """普段の記事(毎日100件)。当日にも同じ量の普段の記事がある前提で、そこに新しい話題を足す。"""
    for k in range(1, days + 1):
        for i in range(per_day):
            add(app, DAY - timedelta(days=k), f"定例の話題{k}日前の{i}番 企業{i}が決算を発表")
    for i in range(per_day):
        add(app, DAY, f"定例の話題0日前の{i}番 企業{i}が決算を発表")


def test_new_topic_is_detected_without_any_dictionary(app):
    quiet_history(app)
    for i in range(14):
        add(app, DAY, f"新型の火星探査ローバー{i}号機が打ち上げへ 各社が参加")
    got = detect_emerging_terms(app.con, DAY, TH, triggers={})
    assert "火星探査" in "".join(t.term for t in got) or any("火星" in t.term for t in got)
    top = got[0]
    assert top.today_count >= 14 and top.ratio >= TH.terms_ratio and top.covered_by == []
    assert top.samples and "火星" in top.samples[0]["title"]


def test_fragments_of_one_term_are_merged_into_the_longest_one(app):
    quiet_history(app)
    for i in range(12):
        add(app, DAY, f"超電導リニア延伸の第{i}報 沿線の自治体が反応")
    got = detect_emerging_terms(app.con, DAY, TH, triggers={})
    terms = [t.term for t in got]
    assert any("超電導リニア" in t for t in terms)
    # 「超電導」「電導リニ」のような断片が、別々の語として並ばない
    assert sum(1 for t in terms if t in "超電導リニア延伸") <= 1


def test_everyday_words_are_not_flagged(app):
    quiet_history(app)
    assert detect_emerging_terms(app.con, DAY, TH, triggers={}) == []


def test_terms_need_enough_history_days(app):
    quiet_history(app, days=3)
    for i in range(14):
        add(app, DAY, f"新型の火星探査ローバー{i}号機が打ち上げへ")
    assert detect_emerging_terms(app.con, DAY, TH, triggers={}) == []          # 比べる日が3日では判定しない


def test_term_covered_by_dictionary_is_labelled(app):
    quiet_history(app)
    for i in range(14):
        add(app, DAY, f"量子コンピュータの新方式{i}を発表")
    got = detect_emerging_terms(app.con, DAY, TH, triggers={"P03": {"words": ["量子コンピュータ"]}})
    assert got and any(t.covered_by == ["P03"] for t in got)


def test_dates_and_numbers_are_not_terms():
    g = _ngrams("10月7日の発表で2026年の7万円台に")
    assert not any(("月" in x and any(c.isdigit() for c in x)) or "万円台" in x for x in g)


def test_relevance_filter_drops_topics_with_no_investment_link(app):
    quiet_history(app)
    for i in range(14):
        add(app, DAY, f"巨大サイクロン襲来 県内で避難指示 第{i}報")
    assert detect_emerging_terms(app.con, DAY, TH, triggers={}, matcher=lambda t: 0) == []
    assert detect_emerging_terms(app.con, DAY, TH, triggers={}, matcher=lambda t: 1) != []


# ---- 全テーマの辞書 ---------------------------------------------------------------------------------------

def test_every_theme_in_themes_md_has_a_dictionary_entry():
    from assoc.pack.build import _load_themes
    themes = set(_load_themes())
    have = set(load_triggers())
    # F04(防衛費増額)は P15、M12(円高メリット)は M11 と同じ語で見る
    assert themes - have <= {"F04", "M12"}, themes - have - {"F04", "M12"}
    assert have <= themes


def test_dictionary_entries_are_well_formed():
    for tid, spec in load_triggers().items():
        assert spec.get("words"), tid
        assert all(len(w) >= 2 for w in spec["words"]), tid
        assert int(spec.get("min_count", 8)) >= 4, tid


# ---- 履歴が短い間(4〜13日)も、辞書のテーマは中央値との比で検知する -----------------------------------------------

def _theme(words=("核融合",), min_count=8):
    return {"P12": {"words": list(words), "min_count": min_count}}


def test_early_history_uses_ratio_against_median_even_without_cold_start_count(app):
    for k in range(1, 6):                                         # 履歴5日: 毎日5件前後
        for i in range(5):
            add(app, DAY - timedelta(days=k), f"{k}日前の核融合の話題{i}")
    for i in range(20):                                           # 当日20件 = 4倍(早期は1.3倍厳しく 2.5×1.3=3.25倍)
        add(app, DAY, f"核融合の新しい動き{i}")
    assert [b.theme_id for b in detect_bursts(app.con, DAY, TH, _theme())] == ["P12"]


def test_early_history_below_ratio_does_not_fire(app):
    for k in range(1, 6):
        for i in range(5):
            add(app, DAY - timedelta(days=k), f"{k}日前の核融合の話題{i}")
    for i in range(12):                                           # 2.4倍: 早期の倍率(3.25倍)に届かない
        add(app, DAY, f"核融合の新しい動き{i}")
    assert detect_bursts(app.con, DAY, TH, _theme()) == []


def test_fewer_than_four_history_days_stays_silent_without_cold_start_count(app):
    for k in range(1, 4):
        for i in range(2):
            add(app, DAY - timedelta(days=k), f"{k}日前の核融合{i}")
    for i in range(30):
        add(app, DAY, f"核融合の新しい動き{i}")
    assert detect_bursts(app.con, DAY, TH, _theme()) == []
