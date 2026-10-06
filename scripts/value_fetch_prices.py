"""Yahoo Finance から日足を一括取得して、銘柄ごとの CSV にする(docs/VALUE_VALIDATION.md §6 順1)。

  python scripts/value_fetch_prices.py --universe data/value/data_j.xlsx --out data/prices --start 2014-01-01

- 国内株(プライム・スタンダード・グロースの内国株式)と、TOPIX 連動 ETF(1306)を取る。
- 再実行すると、すでに取れた銘柄は飛ばす(途中で止まっても続きから)。
- **Yahoo には上場廃止した銘柄が無い**。ここで取れるのは「いま上場している銘柄」だけで、生存者バイアスがある。
- 株価は分割調整済みの終値(配当は調整しない)。出来高も分割調整済みなので、終値×出来高の売買代金は整合する。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

DOMESTIC = ("プライム（内国株式）", "スタンダード（内国株式）", "グロース（内国株式）")


def load_codes(path: Path) -> list[str]:
    df = pd.read_excel(path)
    df = df[df["市場・商品区分"].isin(DOMESTIC)]
    return sorted(str(c).strip() for c in df["コード"])


def fetch_batch(codes: list[str], start: str) -> dict[str, pd.DataFrame]:
    tickers = [f"{c}.T" for c in codes]
    raw = yf.download(tickers, start=start, progress=False, auto_adjust=False, group_by="ticker",
                      threads=True, timeout=30)
    out = {}
    for c, t in zip(codes, tickers):
        try:
            d = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        d = d.dropna(subset=["Close"])
        d = d[d["Volume"].notna()]
        if d.empty:
            continue
        d = d.reset_index().rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                                            "Close": "close", "Volume": "volume"})
        d["date"] = pd.to_datetime(d["date"]).dt.date
        out[c] = d[["date", "open", "high", "low", "close", "volume"]]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2014-01-01")
    ap.add_argument("--batch", type=int, default=80)
    ap.add_argument("--pause", type=float, default=1.5)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    codes = ["1306"] + load_codes(Path(a.universe))
    todo = [c for c in codes if not (out / f"{c}.csv").exists() and not (out / f"{c}.none").exists()]
    print(f"対象 {len(codes)}銘柄、未取得 {len(todo)}銘柄", flush=True)
    done = 0
    for i in range(0, len(todo), a.batch):
        chunk = todo[i:i + a.batch]
        for attempt in range(3):
            try:
                got = fetch_batch(chunk, a.start)
                break
            except Exception as e:                      # 一時的な制限は待って再試行
                print(f"  再試行({attempt + 1}/3): {e}", flush=True)
                time.sleep(10 * (attempt + 1))
        else:
            print("  このバッチは取得できませんでした。次へ進みます", flush=True)
            continue
        for c in chunk:
            if c in got:
                got[c].to_csv(out / f"{c}.csv", index=False)
            else:
                (out / f"{c}.none").write_text("no data", encoding="utf-8")   # 取れなかった印(再実行で飛ばす)
        done += len(chunk)
        print(f"  {done}/{len(todo)}(うち取得 {sum(1 for c in chunk if c in got)}/{len(chunk)})", flush=True)
        time.sleep(a.pause)
    print("完了", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
