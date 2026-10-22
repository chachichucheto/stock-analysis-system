from datetime import datetime, timezone

from assoc.events.digest import build_digest, clean_title
from assoc.events.models import Event


def _ev(i: int, title: str, media: int = 1) -> Event:
    return Event(event_id=f"e{i}", title=title, news_ids=[f"n{i}"], entities=[],
                 first_observed_at=datetime(2026, 10, 21, 12, i, tzinfo=timezone.utc), media_count=media,
                 disclosure_type="", novelty_hash=f"h{i}")


def test_clean_title_strips_media_suffix():
    assert clean_title("日銀総裁、利上げ姿勢維持 - NHKニュース") == "日銀総裁、利上げ姿勢維持"
    assert clean_title("東証、４０６円安（時事通信）") == "東証、４０６円安"


def test_similar_headlines_are_merged_and_ranked_by_count():
    events = [_ev(1, "消費税減税法案が衆院で審議入り 高市首相が答弁"),
              _ev(2, "消費税減税法案 衆院で審議入り 高市首相が答弁へ"),
              _ev(3, "秋田・子吉川にレベル5氾濫特別警報 気象庁が呼びかけ", media=3)]
    digest = build_digest(events, [])
    assert len(digest) == 2
    assert digest[0].startswith("d1 3件 秋田") and digest[1].startswith("d2 2件 消費税減税法案")


def test_noise_and_already_included_titles_are_dropped():
    events = [_ev(1, "株式会社Aが株式会社B＜1234＞株式の大量保有報告書を提出"),
              _ev(2, "岩井コスモ証券にサイバー攻撃 顧客の意図しない売買注文が成立"),
              _ev(3, "豪データセンターＮＥＸＴＤＣ、新規開発凍結 時価総額ほぼ半減", media=2)]
    digest = build_digest(events, ["岩井コスモ証券にサイバー攻撃 顧客の意図しない売買注文が成立"])
    assert len(digest) == 1 and "NEXTDC" not in digest[0] and "ＮＥＸＴＤＣ" in digest[0]


def test_market_price_headlines_collapse_into_one_line():
    events = [_ev(1, "【速報】東証終値1713円安"), _ev(2, "日経平均22日前引け＝反落、703円安の6万3917円"),
              _ev(3, "14時の日経平均は1258円安の6万3362円、アドテストが349.51円押し下げ"),
              _ev(4, "岩井コスモ証券にサイバー攻撃 顧客の意図しない売買注文が成立", media=2),
              _ev(5, "AI彼女 アプリ 選び方 一覧"), _ev(6, "神奈川県で最大震度1の地震 神奈川県・小田原市")]
    digest = build_digest(events, [])
    assert len(digest) == 2
    assert "【相場の値動き】3本" in digest[0] and "サイバー攻撃" in digest[1]


def test_single_article_lines_are_cut():
    events = [_ev(1, "岩井コスモ証券にサイバー攻撃 顧客の意図しない売買注文が成立", media=2),
              _ev(2, "豪NEXTDCが新規開発を凍結 時価総額がほぼ半減")]
    digest = build_digest(events, [])
    assert len(digest) == 1 and "岩井コスモ" in digest[0]


def test_limit():
    topics = ["日銀が追加利上げを決定", "秋田で氾濫特別警報が発表", "岩井コスモ証券にサイバー攻撃", "ゲッティが破産を申請",
              "豪NEXTDCが新規開発を凍結", "小泉防衛相が装備移転に言及"]
    events = [_ev(i, t, media=2) for i, t in enumerate(topics)]
    assert len(build_digest(events, [], limit=4)) == 4
