"""テーマ別の代表銘柄の騰落率(3か月・1か月)を計算して knowledge/theme_returns.json に書く(D36・D37)。

前提カードの「どれだけ織り込まれたか」を、Web 検索ではなく株価データから機械的に出すため。
代表銘柄は knowledge/theme_stocks.yaml。

D37:従来は手で `scripts/theme_returns.py` を動かしたときだけ更新され、株価の補完も別だった。
その結果、サイバー関連が10/5〜10/7に+35〜55%上がっても、入力パックの株価は 9/30 のままだった。
夜の `prepare` が、代表銘柄の株価を補完してから毎回この計算を行う(`refresh`)。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from assoc.config import REPO_ROOT

if TYPE_CHECKING:
    from assoc.app import App

STOCKS_PATH = REPO_ROOT / "knowledge" / "theme_stocks.yaml"
RETURNS_PATH = REPO_ROOT / "knowledge" / "theme_returns.json"


def _ret(df, n: int) -> float | None:
    s = df["close"].dropna()
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


def theme_codes(stocks_path: Path = STOCKS_PATH) -> list[str]:
    spec = yaml.safe_load(stocks_path.read_text(encoding="utf-8")) or {}
    return list(dict.fromkeys(str(c) for codes in spec.values() for c in codes))


def fill_prices(app: "App", day: date, stocks_path: Path = STOCKS_PATH) -> dict:
    """代表銘柄と TOPIX の株価の空白を補う(既存の株価ファイルが数日遅れでも、直近の値が取れるように)。"""
    from assoc.market import layered_prices as lp
    codes = list(dict.fromkeys(theme_codes(stocks_path) + [app.topix_code()]))
    return lp.fill_gap(app.cfg, codes, today=day)


def compute(app: "App", day: date, stocks_path: Path = STOCKS_PATH) -> dict:
    from assoc.market.layered_prices import LayeredPriceSource, layers_from_config
    from assoc.master.names import CompanyMaster
    from assoc.pipeline.evaluate import PriceLoader
    # cfg を渡さない = 古い銘柄をその場で自動補完しない(補完は fill_prices で先に行う。遅いので)
    loader = PriceLoader(LayeredPriceSource(layers_from_config(app.cfg), cfg=None, max_stale_days=3650), day)
    topix = loader.get(app.topix_code())
    master = CompanyMaster.from_db(app.con)
    spec = yaml.safe_load(stocks_path.read_text(encoding="utf-8")) or {}
    out = {"as_of": day.isoformat(), "topix_3m": _ret(topix, 62), "topix_1m": _ret(topix, 21), "themes": {}}
    for tid, codes in spec.items():
        rows = []
        for code in codes:
            df = loader.get(str(code))
            r3, r1 = (_ret(df, 62), _ret(df, 21)) if not df.empty else (None, None)
            # 直近(5営業日)の動きも見る。急に上がり始めたテーマを、1か月・3か月の数字だけでは取り逃がすため(D37)
            r5 = _ret(df, 5) if not df.empty else None
            last = str(df["date"].iloc[-1]) if not df.empty else None
            if last and (day - date.fromisoformat(last)).days > 7:   # 株価が古い銘柄は、騰落率を出さない
                r3 = r1 = r5 = None
            rows.append({"code": str(code), "name": master.names.get(str(code)) or master.names.get(str(code) + "0"),
                         "ret_3m": r3, "ret_1m": r1, "ret_5d": r5, "last_date": last})
        ok = [r for r in rows if r["ret_3m"] is not None]

        def med(k: str):
            v = sorted(r[k] for r in ok if r[k] is not None)
            return v[len(v) // 2] if v else None
        out["themes"][tid] = {"stocks": rows, "median_3m": med("ret_3m"), "median_1m": med("ret_1m"),
                              "median_5d": med("ret_5d"), "n_with_data": len(ok), "n": len(rows)}
    out["topix_5d"] = _ret(topix, 5)
    return out


def refresh(app: "App", day: date, *, fill: bool = True, path: Path = RETURNS_PATH,
            stocks_path: Path = STOCKS_PATH) -> dict:
    """株価を補完して、騰落率を計算し、ファイルに書く。補完に失敗しても、手元の株価で計算は続ける。"""
    info: dict = {}
    if fill:
        try:
            info["fill"] = fill_prices(app, day, stocks_path)
        except Exception as e:  # ネットワーク不通など。計算は手元の株価で続ける
            info["fill_error"] = f"{type(e).__name__}: {e}"
    out = compute(app, day, stocks_path)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    info["themes"] = len(out["themes"])
    info["as_of"] = out["as_of"]
    return info
