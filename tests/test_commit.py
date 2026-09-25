import copy
import json
from pathlib import Path

from assoc.commit.daily import commit_daily, commit_file, validate_schema
from assoc.master.names import CompanyMaster, normalize_name
from assoc.store.records import RecordStore

SAMPLE = Path(__file__).parent / "fixtures" / "daily_output_sample.json"
MASTER = CompanyMaster({"8035": "東京エレクトロン", "6857": "アドバンテスト", "9983": "ファーストリテイリング"},
                       {"ユニクロ": "9983"})


def sample():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_sample_is_valid():
    assert validate_schema(sample()) == []


def test_commit_records_and_rejects_unknown_code(tmp_path):
    store = RecordStore(tmp_path)
    r = commit_daily(sample(), store, MASTER)
    assert r.ok
    assert r.scenario_ids == {"new1": "SC0001"}
    cands = list(store.read("candidate"))
    assert [c["code"] for c in cands] == ["8035"]          # 9999 はマスタに無いので確定しない
    scen = list(store.read("scenario"))[0]
    assert any(x["code"] == "9999" for x in scen["rejected_candidates"])
    assert scen["status"] == "進行中"
    assert all(not store.verify(k) for k in ("grade", "scenario", "candidate", "evidence", "check_plan"))


def test_weak_evidence_downgrades_confirmation(tmp_path):
    store = RecordStore(tmp_path)
    r = commit_daily(sample(), store, MASTER)
    updates = {row["arrow_no"]: row["status"] for row in store.read("check_plan") if row.get("status_update")}
    assert updates == {1: "確認", 2: "未確認"}               # 矢印2は信頼度4の証拠だけ
    assert any("未確認" in w for w in r.warnings)


def test_duplicate_commit_is_refused(tmp_path):
    store = RecordStore(tmp_path)
    assert commit_daily(sample(), store, MASTER).ok
    r = commit_daily(sample(), store, MASTER)
    assert not r.ok and "確定済み" in r.errors[0]


def test_schema_error_is_recorded(tmp_path):
    store = RecordStore(tmp_path)
    doc = sample()
    doc["scenarios"][0]["candidates"][0]["code"] = "ABC"
    r = commit_daily(doc, store, MASTER)
    assert not r.ok
    assert list(store.read("commit_rejection"))
    assert not list(store.read("scenario"))


def test_update_to_unknown_scenario_is_dropped(tmp_path):
    store = RecordStore(tmp_path)
    doc = sample()
    doc["scenario_updates"] = [{"scenario_id": "SC0099", "event_id": "EV1", "type": "強化",
                                "new_fact_summary": "x", "reading_for": "x", "reading_against": "x",
                                "strength_after": "Strong"}]
    r = commit_daily(doc, store, MASTER)
    assert r.ok and not list(store.read("scenario_update"))


def test_single_type_expected_days_is_clamped(tmp_path):
    store = RecordStore(tmp_path)
    doc = sample()
    doc["scenarios"][0]["expected_days"] = 45
    commit_daily(doc, store, MASTER)
    assert list(store.read("scenario"))[0]["expected_days"] == 20


def test_commit_file_moves_to_processed(tmp_path):
    store = RecordStore(tmp_path / "records")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "2026-10-01.json"
    f.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert commit_file(f, store, MASTER).ok
    assert (inbox / "processed" / "2026-10-01.json").exists()


def test_name_matching():
    assert normalize_name("株式会社 東京エレクトロン") == normalize_name("東京エレクトロン")
    assert MASTER.check("8035", "東京エレクトロン株式会社").ok
    assert MASTER.check("9983", "ユニクロ").ok
    assert not MASTER.check("8035", "アドバンテスト").ok
    assert not CompanyMaster({}).check("8035", "東京エレクトロン").ok
