from datetime import datetime, timezone

from assoc.events.cluster import ClusterInput, cluster
from assoc.events.companies import CompanyIndex
from assoc.master.names import CompanyMaster

NAMES = {
    "9449": "ＧＭＯインターネットグループ", "4784": "ＧＭＯインターネット", "3769": "ＧＭＯペイメントゲートウェイ",
    "7203": "トヨタ自動車", "6758": "ソニーグループ", "9999": "ランド", "8035": "東京エレクトロン",
}
ALIASES = {"トヨタ": "7203", "ソニー": "6758", "TEL": "8035"}


def _index(stop=None):
    return CompanyIndex.from_master(CompanyMaster(NAMES), ALIASES, stop)


def _item(i, title, source):
    return ClusterInput(id=f"n{i}", title=title, kind="news", source=source,
                        first_observed_at=datetime(2026, 10, 28, 12, i, tzinfo=timezone.utc))


def test_ascii_brand_and_full_name_are_found():
    idx = _index()
    keys = {m.key for m in idx.find("GMO、不正アクセスで顧客情報が流出")}
    assert "gmo" in keys
    assert any(m.group and set(m.codes) == {"9449", "4784", "3769"} for m in idx.find("GMOに攻撃"))
    assert [m.codes for m in idx.find("ＧＭＯペイメントゲートウェイが決算を発表")][0] == ("3769",)


def test_short_ascii_alias_needs_word_boundary():
    idx = _index()
    assert not idx.find("intelが新型GPUを発表")           # 'TEL' は intel の中には当たらない
    assert [m.codes for m in idx.find("TELが上方修正")] == [("8035",)]


def test_katakana_name_does_not_match_inside_longer_word_and_stop_list_works():
    idx = _index()
    assert not idx.find("ディズニーランドー周辺")
    assert [m.codes for m in idx.find("ランドが急騰")] == [("9999",)]
    assert not _index(stop={"ランド"}).find("ランドが急騰")


def test_media_tail_is_not_a_company():
    assert not _index().find("日フィンランド防衛相会談（ソニー系（ANN））")


def test_split_headlines_about_one_company_and_topic_are_merged():
    items = [
        _item(1, "ハッカー集団 GMOに100万ドル要求 不正アクセスで顧客脅迫も", "NHK"),
        _item(2, "社内ホワイトハッカー「誰より悔しい」不正アクセス受けたGMO熊谷社長", "産経新聞"),
        _item(3, "GMO不正アクセス 警視庁が捜査", "Yahoo!ニュース"),
        _item(4, "GMOペイメントゲートウェイ、中間決算で最高益 手数料収入が伸びる", "日本経済新聞"),
    ]
    plain = cluster(items)
    merged = cluster(items, company_index=_index())
    assert len(plain) == 4
    sizes = sorted(e.media_count for e in merged)
    assert sizes == [1, 3]                                  # 不正アクセスの3本は1つに、決算は別
    story = max(merged, key=lambda e: e.media_count)
    assert "gmo" in story.story_keys and "9449" in story.mentioned_codes


def test_same_company_different_topic_is_not_merged():
    items = [_item(1, "トヨタ、九州の新工場で電池の生産を開始へ 投資額は数千億円", "A"),
             _item(2, "トヨタが豪州の販売会社で不正経理が発覚 調査委員会を設置", "B")]
    assert len(cluster(items, company_index=_index())) == 2
