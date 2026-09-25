from __future__ import annotations

from datetime import date

from assoc.ingest.tdnet import decode_tdnet_html, parse_list_page
from ingest_helpers import fixture_bytes


def test_decode_tdnet_html_handles_utf8():
    text = decode_tdnet_html(fixture_bytes("tdnet_list_sample.html"))
    assert "サンプル物産株式会社" in text


def test_parse_list_page_extracts_rows():
    html = decode_tdnet_html(fixture_bytes("tdnet_list_sample.html"))
    items = parse_list_page(html, list_date=date(2026, 9, 25))
    assert len(items) == 3
    first = items[0]
    assert first.code == "13010"
    assert first.company_name == "サンプル物産株式会社"
    assert "大量保有報告書" in first.title
    assert first.url.endswith("140120260925500001.pdf")
    assert first.source == "tdnet"
    assert first.published_at is not None
    assert first.published_at.hour == 15 and first.published_at.minute == 0


def test_parse_list_page_disclosure_id_is_stable():
    html = decode_tdnet_html(fixture_bytes("tdnet_list_sample.html"))
    items1 = parse_list_page(html, list_date=date(2026, 9, 25))
    items2 = parse_list_page(html, list_date=date(2026, 9, 25))
    assert items1[0].disclosure_id == items2[0].disclosure_id
    assert len({i.disclosure_id for i in items1}) == 3  # 重複なし


def test_parse_list_page_empty_returns_empty_list():
    assert parse_list_page("<html><body><table></table></body></html>", list_date=date(2026, 9, 25)) == []
