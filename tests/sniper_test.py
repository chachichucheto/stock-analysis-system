from datetime import datetime, timezone

from assoc.events.companies import CompanyIndex
from assoc.events.models import Event
from assoc.events.sniper import classify, detect_sniper
from assoc.master.names import CompanyMaster

NAMES = {"9449": "ＧＭＯインターネットグループ", "4784": "ＧＭＯインターネット", "3769": "ＧＭＯペイメントゲートウェイ",
         "4689": "ＬＩＮＥヤフー", "2371": "カカクコム", "7203": "トヨタ自動車", "4684": "オービック"}
ALIASES = {"GMO": "9449", "トヨタ": "7203"}


def _index():
    return CompanyIndex.from_master(CompanyMaster(NAMES), ALIASES)


def _ev(i, title, media=1, disclosure=False, codes=None):
    return Event(event_id=f"e{i}", title=title, news_ids=[f"n{i}"], entities=[],
                 first_observed_at=datetime(2026, 10, 28, 12, i, tzinfo=timezone.utc), media_count=media,
                 disclosure_type="disclosure" if disclosure else "", novelty_hash=f"h{i}", codes=codes or [])


def test_classify_picks_a_type_and_drops_routine_disclosures():
    assert classify("GMO、不正アクセスで顧客情報が流出")[0] == "事故・不祥事"
    assert classify("2027年3月期通期業績予想の修正(上方修正)に関するお知らせ")[0] == "業績修正"
    assert classify("2026年12月期第3四半期決算短信〔日本基準〕(連結)") is None
    assert classify("決算説明資料 業績予想の修正について")[0] == "業績修正"     # 定型の題名でも修正の語があれば材料
    assert classify("今日の天気は晴れ") is None


def test_single_media_incident_of_a_named_company_passes_but_weak_type_does_not():
    ev_inc = _ev(1, "GMO、不正アクセスで顧客情報が流出", media=1)
    ev_ma = _ev(2, "トヨタが豪州の販売会社を子会社化", media=1)            # M&A は重み1。確かさが無ければ通さない
    hits = detect_sniper([ev_inc, ev_ma], _index(), liquid=None)
    assert [(h.code, h.type) for h in hits] == [("9449", "事故・不祥事")]   # GMO は別名辞書の中心の1社になる


def test_illiquid_stock_and_recent_key_are_dropped():
    ev = _ev(1, "トヨタ、通期業績予想を上方修正", media=1)
    assert detect_sniper([ev], _index(), liquid={"9999"}) == []
    assert len(detect_sniper([ev], _index(), liquid={"7203"})) == 1
    assert detect_sniper([ev], _index(), liquid={"7203"}, recent_keys={"7203:業績修正"}) == []


def test_tob_keeps_both_companies_and_stale_reprint_is_skipped():
    ev = _ev(1, "LINEヤフー、カカクコムにTOB 価格を3700円に引き上げ", media=2)
    h = detect_sniper([ev], _index(), liquid=None)[0]
    assert h.type == "TOB" and set(h.mentioned_codes) == {"4689", "2371"}
    old = _ev(2, "トヨタ、新会社にTOB実施を発表（2024年2月5日掲載）", media=3)
    assert detect_sniper([old], _index(), liquid=None) == []


def test_issuer_disclosure_counts_as_corroboration():
    ev = _ev(1, "不正アクセスに関するお知らせ", media=1, disclosure=True, codes=["4684"])
    h = detect_sniper([ev], _index(), liquid=None)[0]
    assert h.code == "4684" and "本人の開示あり" in h.reasons
