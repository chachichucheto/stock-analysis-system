"""Yahoo Finance の年次の財務を取り、財務データ(financials.csv の形式)にする。**キーなしの代用。精度は低い。**

  python scripts/value_fetch_yahoo_financials.py --universe data/value/data_j.xlsx --out data/value/yahoo_financials.csv

限界(結果を読むときに必ず考慮する):
  - 取れるのは**年次のみ・直近4〜5期**(2021年ごろから)。TTM・四半期の加速(型C)は作れない。型B の本決算5期は4期に緩める必要がある
  - **開示日は取れない**。期末の90日後を開示日とみなす(決算短信は45日、有価証券報告書は3か月以内が期限なので、
    先読みは起きない保守的な仮定。そのぶん、実際より情報が遅れて見える)
  - 発行済株式数は期末時点の値で、**その後の株式分割で調整されていない**ことがある(Yahoo の株価は分割調整済み)。
    分割の記録(splits)で、期末より後の分割を掛けて合わせる
  - 項目の定義は Yahoo 独自で、日本基準の勘定科目と一致しない場合がある(売掛金、投資有価証券など)
  - 上場廃止銘柄が無い(生存者バイアス)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import value_run_price_test as base  # noqa: E402

LAG_DAYS = 90

BS_MAP = {"cash": ["Cash And Cash Equivalents"], "receivables": ["Accounts Receivable"],
          "securities": ["Other Short Term Investments"], "current_assets": ["Current Assets"],
          "total_assets": ["Total Assets"], "total_liabilities": ["Total Liabilities Net Minority Interest"],
          "equity": ["Stockholders Equity"], "interest_debt": ["Total Debt"]}


def _get(df: pd.DataFrame, names: list[str], col) -> float | None:
    for n in names:
        if n in df.index:
            v = df.loc[n, col]
            if pd.notna(v):
                return float(v)
    return None


def fetch_one(code: str) -> list[dict]:
    t = yf.Ticker(f"{code}.T")
    bs, inc = t.balance_sheet, t.income_stmt
    if bs is None or bs.empty:
        return []
    splits = t.splits
    rows = []
    for col in bs.columns:
        pe = pd.Timestamp(col).date()
        shares = _get(bs, ["Ordinary Shares Number"], col)
        if shares is not None and splits is not None and len(splits):
            after = splits[[pd.Timestamp(d).date() > pe for d in splits.index]]
            for ratio in after:
                shares *= float(ratio)                       # 期末より後の分割を掛けて、いまの株価(分割調整済み)に合わせる
        inv = _get(bs, ["Investments And Advances"], col)
        if inv is None:
            parts = [_get(bs, [n], col) for n in ("Investmentin Financial Assets", "Available For Sale Securities", "Long Term Equity Investment")]
            parts = [p for p in parts if p is not None]
            inv = max(parts) if parts else None             # 重複を避けるため、足さずに最大のものを使う
        allow = _get(bs, ["Receivables Adjustments Allowances"], col)
        row = {"code": code, "period_end": pe.isoformat(), "disclosed_date": (pe + timedelta(days=LAG_DAYS)).isoformat(),
               "period": "FY", "investment_securities": inv, "allowance": abs(allow) if allow is not None else None,
               "shares_ex_treasury": shares}
        for k, names in BS_MAP.items():
            row[k] = _get(bs, names, col)
        if inc is not None and not inc.empty and col in inc.columns:
            row["revenue_ttm"] = _get(inc, ["Total Revenue"], col)
            row["operating_income_ttm"] = _get(inc, ["Operating Income"], col)
            row["net_income_ttm"] = _get(inc, ["Net Income Common Stockholders", "Net Income"], col)
        if row["total_assets"] is None and row["equity"] is None:
            continue                                        # 中身が空の期は捨てる
        rows.append(row)
    return rows


def safe_fetch(code: str, cache: Path, pause: float = 0.0) -> list[dict]:
    """空の応答は、アクセス制限(429)の可能性があるので、保存せず再試行する(上場直後などで本当に空の銘柄は、3回目で空と確定)。"""
    f = cache / f"{code}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    for attempt in range(3):
        try:
            time.sleep(pause)
            rows = fetch_one(code)
            if rows or attempt == 2:
                f.write_text(json.dumps(rows), encoding="utf-8")
                return rows
            time.sleep(8 * (attempt + 1))
            continue
        except Exception as e:                              # 一時的な制限は待って再試行
            time.sleep(5 * (attempt + 1))
            last = e
    print(f"  失敗 {code}: {last}", flush=True)
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--pause", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    _, small = base.load_universe(Path(a.universe))
    codes = sorted(small)[: a.limit or None]
    cache = Path(a.out).parent / "yf_fin_cache"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"対象 {len(codes)}銘柄", flush=True)
    all_rows, done = [], 0
    with ThreadPoolExecutor(a.threads) as ex:
        for rows in ex.map(lambda c: safe_fetch(c, cache, a.pause), codes):
            all_rows += rows
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(codes)}(財務の行 {len(all_rows)})", flush=True)
    df = pd.DataFrame(all_rows)
    df.to_csv(a.out, index=False, encoding="utf-8")
    print(f"完了 {len(df)}行 / {df['code'].nunique() if len(df) else 0}銘柄 → {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
