"""テーマ別の代表銘柄の騰落率(3か月・1か月・5日、TOPIX 超過)を計算して knowledge/theme_returns.json に書く。

本体は src/assoc/market/theme_returns.py(夜の `prepare` からも呼ばれる。D37)。ここは手で動かす入口。
使い方: ./.venv/Scripts/python.exe scripts/theme_returns.py [--date YYYY-MM-DD] [--no-fill]
"""
from __future__ import annotations

import argparse
from datetime import date

from assoc.app import App
from assoc.config import load_config
from assoc.market import theme_returns


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--no-fill", action="store_true", help="株価の補完(ネットワーク)をしない")
    a = ap.parse_args()
    app = App(load_config(None))
    day = date.fromisoformat(a.date) if a.date else app.today()
    info = theme_returns.refresh(app, day, fill=not a.no_fill)
    print(f"書き出し: {info['themes']} テーマ(基準日 {info['as_of']})", info.get("fill_error", ""))


if __name__ == "__main__":
    main()
