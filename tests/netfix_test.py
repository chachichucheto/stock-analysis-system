from __future__ import annotations

from pathlib import Path

from assoc import netfix


def test_no_change_when_certifi_path_is_ascii(monkeypatch, tmp_path):
    import certifi

    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "cacert.pem"))
    assert netfix.ensure_ascii_ca_bundle() is None


def test_copies_to_ascii_path_when_certifi_path_is_not_ascii(monkeypatch, tmp_path):
    import certifi

    src_dir = tmp_path / "日本語フォルダ"
    src_dir.mkdir()
    src = src_dir / "cacert.pem"
    src.write_text("dummy-ca", encoding="utf-8")
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.setattr(certifi, "where", lambda: str(src))
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "ascii_tmp"))
    (tmp_path / "ascii_tmp").mkdir()

    got = netfix.ensure_ascii_ca_bundle()
    assert got is not None and got.isascii()
    assert Path(got).read_text(encoding="utf-8") == "dummy-ca"
    import os
    assert os.environ["CURL_CA_BUNDLE"] == got
    assert netfix.ensure_ascii_ca_bundle() is None       # 設定済みなら何もしない
