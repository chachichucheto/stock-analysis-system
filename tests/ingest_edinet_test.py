from __future__ import annotations

import json

from assoc.ingest.edinet import fetch_edinet, parse_documents_json
from ingest_helpers import fixture_text, make_config, make_db


def _load():
    return json.loads(fixture_text("edinet_documents_sample.json"))


def test_parse_documents_json_filters_to_target_doc_types():
    items = parse_documents_json(_load())
    assert len(items) == 2  # docTypeCode 350・360 のみ(120 は対象外)
    doc_types = {i.doc_type for i in items}
    assert doc_types == {"350", "360"}


def test_parse_documents_json_maps_sec_code_to_4digit_ticker():
    items = parse_documents_json(_load())
    by_id = {i.disclosure_id: i for i in items}
    codes = {i.code for i in items}
    assert "1301" in codes  # secCode "13010" -> "1301"
    assert None in codes  # secCode が無い(個人の変更報告書)


def test_fetch_edinet_skips_when_api_key_missing(tmp_path):
    cfg = make_config()  # edinet.api_key は空
    con = make_db(tmp_path)
    n = fetch_edinet(cfg, con)
    assert n == 0
    logged = con.execute("SELECT source, ok, message FROM fetch_log WHERE source = 'edinet'").fetchall()
    assert len(logged) == 1
    assert logged[0][1] is True
    assert "未設定" in logged[0][2]
