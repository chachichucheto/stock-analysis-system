"""テーマ別の見出し件数の急増(events/burst.py。D37)。

背景:2026-10-07、別々の会社の不正アクセス報道が連日17〜107件出てサイバー関連株が急騰したのに、
足切りは1件ごとの判定だったので、通過0件のまま見逃した。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from assoc.app import App
from assoc.config import Config, Thresholds
from assoc.events.burst import count_by_day, detect_bursts, story_key
from assoc.timeutil import JST

P06 = {"P06": {"words": ["不正アクセス", "情報漏えい", "ランサム"], "min_count": 15, "ratio": 2.0, "cold_start_count": 20}}
NO_COLD = {"P09": {"words": ["原発"], "min_count": 10}}      # cold_start_count なし
TH = Thresholds()
DAY = date(2026, 10, 7)


@pytest.fixture
def app(tmp_path):
    cfg = Config(raw={"paths": {"data_dir": str(tmp_path / "data")}, "prices": {"source": "csv_dir"}, "collect": {}})
    return App(cfg)


def _at(d: date, hour: int = 12) -> datetime:
    return datetime.combine(d, time(hour), tzinfo=JST)


_n = 0


def add(app, d: date, title: str, *, source="ITmedia NEWS", published=None, hour=12):
    global _n
    _n += 1
    obs = _at(d, hour)
    app.con.execute("INSERT INTO news_item VALUES (?, ?, 2, ?, '', ?, 'ja', ?, ?, ?)",
                    [f"n{_n}", source, title, f"https://example.com/{_n}", published or obs, obs, f"h{_n}"])


def add_breaches(app, d: date, n: int, *, word="不正アクセス"):
    for i in range(n):
        add(app, d, f"{d.isoformat()}の{i}番目の会社に{word} 個人情報が流出か")


def mark_collected(app, d: date):
    add(app, d, "天気予報 全国的に晴れ")      # 語を含まない記事(収集した日として数えるため)


def test_story_key_merges_reprints_with_different_outlet_suffix():
    a = story_key("大和証券、顧客11万人の口座番号など漏洩か 委託先に不正アクセス - ITmedia")
    b = story_key("大和証券、顧客11万人の口座番号など漏洩か 委託先に不正アクセス - 朝日新聞")
    assert a == b


def test_cold_start_fires_for_theme_with_cold_start_count(app):
    add_breaches(app, DAY, 25)
    bursts = detect_bursts(app.con, DAY, TH, P06)
    assert [b.theme_id for b in bursts] == ["P06"]
    b = bursts[0]
    assert b.today_count == 25 and b.cold_start is True
    assert len(b.samples) >= 1


def test_cold_start_does_not_fire_below_threshold(app):
    add_breaches(app, DAY, 12)
    assert detect_bursts(app.con, DAY, TH, P06) == []


def test_theme_without_cold_start_count_is_silent_until_history_accumulates(app):
    for i in range(60):
        add(app, DAY, f"原発{i}号機の運転計画 {i}")
    assert detect_bursts(app.con, DAY, TH, NO_COLD) == []


def test_same_story_reprinted_counts_once(app):
    for outlet in ["ITmedia", "朝日新聞", "NHK", "時事通信"] * 10:
        add(app, DAY, f"A社に不正アクセス 個人情報が漏えいか - {outlet}")
    assert count_by_day(app.con, P06["P06"]["words"], DAY, DAY)[0][DAY] == 1


def test_promotional_headline_without_incident_words_is_not_counted(app):
    for i in range(30):
        add(app, DAY, f"サイバーセキュリティアワード{i} 募集開始")
    assert detect_bursts(app.con, DAY, TH, P06) == []


def test_old_articles_picked_up_on_first_collection_are_not_counted(app):
    for i in range(30):
        add(app, DAY, f"{i}番目の不正アクセス 昔の話", published=_at(DAY - timedelta(days=20)))
    assert detect_bursts(app.con, DAY, TH, P06) == []


def test_steady_state_uses_ratio_against_median(app):
    for k in range(1, 21):                                  # 20日分の履歴: 毎日5件前後
        d = DAY - timedelta(days=k)
        add_breaches(app, d, 5)
    add_breaches(app, DAY, 16)                              # 当日16件 = 3倍超
    b = detect_bursts(app.con, DAY, TH, P06)
    assert [x.theme_id for x in b] == ["P06"]
    assert b[0].cold_start is False and b[0].baseline == 5 and b[0].ratio == 3.2


def test_steady_state_normal_day_does_not_fire(app):
    for k in range(0, 21):
        add_breaches(app, DAY - timedelta(days=k), 16 if k % 2 else 18)   # いつも多いテーマ。当日18件は普段の範囲
    assert detect_bursts(app.con, DAY, TH, P06) == []


def test_steady_state_small_numbers_do_not_fire_even_at_high_ratio(app):
    for k in range(1, 21):
        add_breaches(app, DAY - timedelta(days=k), 1)
    add_breaches(app, DAY, 6)                               # 6倍だが下限(15)未満
    assert detect_bursts(app.con, DAY, TH, P06) == []


def test_persistent_burst_survives_a_quiet_weekend_day(app):
    # 9/30(水)〜10/2(金)に多く、10/3(土)に減っても、直近7日のうち3日以上が「増えた日」なら急増のまま
    for d, n in [(date(2026, 9, 30), 30), (date(2026, 10, 1), 28), (date(2026, 10, 2), 26), (date(2026, 10, 3), 6)]:
        add_breaches(app, d, n)
    b = detect_bursts(app.con, date(2026, 10, 3), TH, P06)
    assert [x.theme_id for x in b] == ["P06"] and b[0].elevated_days == 3


def test_days_without_any_collection_are_not_counted_as_zero(app):
    # 収集が止まっていた日(記事が1件もない日)を0件と数えると、中央値が下がって誤検知になる
    for k in (10, 9, 8, 7, 6, 5, 4, 3, 2):
        mark_collected(app, DAY - timedelta(days=k))
        add_breaches(app, DAY - timedelta(days=k), 14)
    add_breaches(app, DAY, 18)
    assert detect_bursts(app.con, DAY, TH, P06) == []


def test_sample_headlines_put_pr_last_and_limit_per_outlet(app):
    for i in range(25):
        add(app, DAY, f"{i}番目の会社に不正アクセス", source="PRTIMES プレスリリース")
    add(app, DAY, "大手に不正アクセス 顧客情報が漏えい", source="NHK 経済")
    b = detect_bursts(app.con, DAY, TH, P06)[0]
    assert b.samples[0]["media"] == "NHK"
    assert sum(1 for s in b.samples if s["media"] == "PRTIMES") <= 2


def test_real_trigger_file_loads_and_p06_ignores_events_announcements():
    from assoc.events.burst import load_triggers
    t = load_triggers()
    assert "P06" in t and "不正アクセス" in t["P06"]["words"]
    assert "サイバーセキュリティ" not in t["P06"]["words"]      # 販促・イベント告知を数えないため


def test_story_key_does_not_cut_katakana_long_vowel_or_distinguishes_stories():
    # 長音「ー」を媒体名の区切りと取り違えて、別の記事を1件にまとめない(D39で修正した不具合)
    a = story_key("新型の火星探査ローバー1号機が打ち上げへ 各社が参加")
    b = story_key("新型の火星探査ローバー2号機が打ち上げへ 各社が参加")
    assert a != b
    assert story_key("サーバー攻撃でA社が被害 - ITmedia") == story_key("サーバー攻撃でA社が被害 | 朝日新聞")
    assert story_key("サーバー攻撃でA社が被害 - ITmedia") != story_key("サーバー攻撃でB社が被害 - ITmedia")
