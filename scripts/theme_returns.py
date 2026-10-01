"""テーマ別の代表銘柄の騰落率(3か月・1か月、TOPIX 超過)を計算して knowledge/theme_returns.json に書く。

前提カードの「どれだけ織り込まれたか」を、Web 検索ではなく株価データから機械的に出すため(D36)。
代表銘柄は knowledge/theme_stocks.yaml。株価は設定の価格ソース(既存の株価ファイルを読み取り専用で重ねたもの)。
使い方: ./.venv/Scripts/python.exe scripts/theme_returns.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import yaml

from assoc.app import App
from assoc.config import load_config
from assoc.market.layered_prices import LayeredPriceSource, layers_from_config
from assoc.master.names import CompanyMaster
from assoc.pipeline.evaluate import PriceLoader

ROOT = Path(__file__).resolve().parent.parent


def _ret(df, n: int) -> float | None:
    s = df["close"].dropna()
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    app = App(load_config(None))
    day = date.fromisoformat(a.date) if a.date else app.today()
    # cfg を渡さない = 古い銘柄をその場で自動補完しない(補完は `assoc prices fill` で先に行う。遅いので)
    loader = PriceLoader(LayeredPriceSource(layers_from_config(app.cfg), cfg=None, max_stale_days=3650), day)
    topix = loader.get(app.topix_code())
    master = CompanyMaster.from_db(app.con)
    spec = yaml.safe_load((ROOT / "knowledge" / "theme_stocks.yaml").read_text(encoding="utf-8"))
    out = {"as_of": day.isoformat(), "topix_3m": _ret(topix, 62), "topix_1m": _ret(topix, 21), "themes": {}}
    for tid, codes in spec.items():
        rows = []
        for code in codes:
            df = loader.get(str(code))
            r3, r1 = (_ret(df, 62), _ret(df, 21)) if not df.empty else (None, None)
            last = str(df["date"].iloc[-1]) if not df.empty else None
            if last and (day - date.fromisoformat(last)).days > 7:   # 株価が古い銘柄は、騰落率を出さない(補完してから)
                r3 = r1 = None
            rows.append({"code": str(code), "name": master.names.get(str(code)) or master.names.get(str(code) + "0"),
                         "ret_3m": r3, "ret_1m": r1, "last_date": last})
        ok = [r for r in rows if r["ret_3m"] is not None]
        med = lambda k: sorted(r[k] for r in ok if r[k] is not None)[len([r for r in ok if r[k] is not None]) // 2] if ok else None
        out["themes"][tid] = {"stocks": rows, "median_3m": med("ret_3m"), "median_1m": med("ret_1m"),
                              "n_with_data": len(ok), "n": len(rows)}
    (ROOT / "knowledge" / "theme_returns.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"書き出し: {len(out['themes'])} テーマ(基準日 {day})")


if __name__ == "__main__":
    main()
