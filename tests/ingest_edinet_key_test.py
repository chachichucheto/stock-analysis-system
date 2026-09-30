from __future__ import annotations

from datetime import date

import pytest

from assoc.config import Config
from assoc.ingest import edinet
from assoc.store.db import connect


def test_resolve_api_key_order(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(edinet, "_dotenv_path", lambda: env_file)
    env_file.write_text('EDINET_API_KEY="dummy-from-dotenv"\n', encoding="utf-8")

    assert edinet.resolve_api_key(Config(raw={"edinet": {"api_key": "dummy-from-config"}})) == "dummy-from-config"
    assert edinet.resolve_api_key(Config(raw={"edinet": {"api_key": ""}})) == "dummy-from-dotenv"
    monkeypatch.setenv("EDINET_API_KEY", "dummy-from-env")
    assert edinet.resolve_api_key(Config(raw={"edinet": {}})) == "dummy-from-env"


def test_resolve_api_key_empty_when_nothing_set():
    assert edinet.resolve_api_key(Config(raw={})) == ""


def test_fetch_failure_message_does_not_contain_key(tmp_path, monkeypatch):
    class Boom:
        def get(self, url, params=None):
            raise ConnectionError(f"failed for url: {url}?Subscription-Key={params['Subscription-Key']}")

    monkeypatch.setattr(edinet, "RateLimiter", lambda *a, **k: Boom())
    con = connect(tmp_path)
    cfg = Config(raw={"edinet": {"api_key": "dummy-secret-key"}, "collect": {}})
    with pytest.raises(RuntimeError) as ei:
        edinet.fetch_edinet(cfg, con, day=date(2026, 9, 30))
    assert "dummy-secret-key" not in str(ei.value)
    assert ei.value.__cause__ is None
