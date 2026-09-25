from __future__ import annotations

import json

from assoc.ingest.gdelt import parse_timeline_json
from ingest_helpers import fixture_text


def test_parse_timeline_json_aggregates_by_day():
    data = json.loads(fixture_text("gdelt_timeline_sample.json"))
    points = parse_timeline_json(data, keyword="半導体")
    by_date = {p.date: p.value for p in points}
    assert set(by_date) == {"2026-09-23", "2026-09-24", "2026-09-25"}
    assert by_date["2026-09-23"] == (0.01 + 0.03) / 2
    assert by_date["2026-09-25"] == (0.10 + 0.20) / 2


def test_parse_timeline_json_empty_when_no_timeline():
    assert parse_timeline_json({}, keyword="x") == []
