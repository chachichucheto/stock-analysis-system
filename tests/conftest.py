from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_edinet_key(tmp_path_factory, monkeypatch):
    """テストが、本物の .env / 環境変数の EDINET API キーを拾って実通信しないようにする。"""
    from assoc.ingest import edinet

    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    empty = tmp_path_factory.mktemp("no_dotenv") / ".env"     # 存在しないファイル
    monkeypatch.setattr(edinet, "_dotenv_path", lambda: empty)


@pytest.fixture(autouse=True)
def _isolate_theme_returns(tmp_path_factory, monkeypatch):
    """テストの結果が、本物の knowledge/theme_returns.json(その日の株価)で左右されないようにする(株価の急変イベント。D40)。"""
    from assoc.market import theme_returns

    monkeypatch.setattr(theme_returns, "RETURNS_PATH", tmp_path_factory.mktemp("no_returns") / "theme_returns.json")
