"""Windows で、日本語を含むフォルダに仮想環境があると yfinance が SSLError で失敗する問題への対処。

原因(2026-09-30 に確認): yfinance が使う通信部品(curl_cffi / libcurl)は、証明書ファイル(certifi の
cacert.pem)のパスに日本語などの非 ASCII 文字が含まれると読み込めない
(`curl: (77) error adding trust anchors`)。仮想環境が `...\\クロード連想モデル\\.venv\\` にあると必ず起きる。
`requests`(Python が読む)は影響を受けないため、収集器(RSS など)は動くのに株価だけ失敗する、という
分かりにくい症状になる。

対処: 証明書のパスが非 ASCII なら、ASCII だけのパス(一時フォルダの中)へコピーし、環境変数
`CURL_CA_BUNDLE` でそちらを使わせる。何度呼んでも安全(既にコピー済みなら何もしない)。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def ensure_ascii_ca_bundle() -> str | None:
    """必要なら証明書を ASCII パスへコピーして環境変数を設定する。設定したパスを返す(不要なら None)。"""
    if os.environ.get("CURL_CA_BUNDLE") and str(os.environ["CURL_CA_BUNDLE"]).isascii():
        return None
    try:
        import certifi
    except ImportError:
        return None
    src = Path(certifi.where())
    if str(src).isascii():
        return None
    for base in (Path(tempfile.gettempdir()), Path(os.environ.get("PUBLIC", "C:/Users/Public"))):
        if not str(base).isascii():
            continue
        dst = base / "assoc_ca" / "cacert.pem"
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copyfile(src, dst)
        except OSError:
            continue
        os.environ["CURL_CA_BUNDLE"] = str(dst)
        return str(dst)
    return None
