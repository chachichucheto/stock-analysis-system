"""型A(資産バリュー)・型B(シクリカル底)の検証。**Yahoo の年次財務による代用版。参考値**(docs/VALUE_VALIDATION.md §7.5)。

  python scripts/value_fetch_yahoo_financials.py --universe data/value/data_j.xlsx --out data/value/yahoo_financials.csv
  python scripts/value_run_fin_test.py

**事前登録(結果を見る前に固定)**
  - 財務は Yahoo の年次のみ(直近4〜5期)。開示日は期末+90日とみなす(保守的。先読みなし)。
    → 検証できる基準日は 2022-07 〜 2025-09 の約39日(約3年)。**G1 の基準(両期間・各24基準日以上)を満たせない**。
      したがって判定は「参考値」。結論は「合格」ではなく、方向の確認に留める
  - H1'(型A):型A × 120営業日。日付ごとの差が正、t>2
  - H2'(型B):型B × 250営業日。同上。本決算の履歴は5期→4期に緩める(Yahoo は4〜5期のため)
  - 型A は 型A全体、さらに「ネットネット指数<1 のみ」「PBR<0.5かつ自己資本比率60%以上のみ」を内訳で出す(探索)
  - 型C(利益の加速)は、四半期・TTM が取れないので検証しない
  - 基準日は月末の最終営業日、対象・コスト(往復1%)・足切りは株価だけの検証と同じ。生存者バイアスあり
  - 防ぎきれない偏り:2022〜2025年は日本株でバリュー株・低PBR株が物色された局面(東証の要請など)。型Aに有利な相場
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assoc.market.prices import CsvDirPriceSource  # noqa: E402
from assoc.value import backtest as bt  # noqa: E402
from assoc.value.data import FinancialsStore  # noqa: E402
from assoc.value.thresholds import ValueThresholds  # noqa: E402
import value_run_price_test as base  # noqa: E402

COST = 0.010
FIN_COLS = ["cash", "receivables", "securities", "investment_securities", "allowance", "current_assets", "total_assets",
            "total_liabilities", "equity", "interest_debt", "revenue_ttm", "operating_income_ttm", "net_income_ttm",
            "shares_ex_treasury"]


def main() -> int:
    uni, small = base.load_universe(Path("data/value/data_j.xlsx"))
    px = CsvDirPriceSource(Path("data/prices"))
    topix = px.daily("1306", date(2013, 1, 1), date(2026, 12, 31))
    raw = pd.read_csv("data/value/yahoo_financials.csv", dtype={"code": str})
    # FinancialsStore.from_csv の形式(空は None)に合わせて、いったん書き出して読む
    tmp = Path("data/value/yahoo_financials_for_store.csv")
    raw.to_csv(tmp, index=False)
    fin = FinancialsStore.from_csv(tmp)
    print(f"財務 {len(fin.rows)}行 / {len(fin.codes())}銘柄", flush=True)
    th = ValueThresholds(cyc_min_fy_rows=4, horizons=(20, 60, 120, 250))
    dates = bt.month_end_dates(date(2022, 7, 1), date(2025, 9, 30))
    res = bt.run_backtest(uni, fin, px, topix, dates, th)
    res.obs.to_csv("data/reports/value_fin_test_obs.csv", index=False, encoding="utf-8-sig")
    obs = res.obs
    print("観測", len(obs), "基準日", obs["asof"].nunique(), "警告", res.warnings, flush=True)

    def row(name, m, h):
        r = bt.date_paired_lift(obs, m, f"excess_final_{h}")
        n = int((m & obs[f"excess_final_{h}"].notna()).sum())
        net = r["lift"] - COST
        return f"| {name} | {h}営業日 | {n} | {r['n_dates']} | {r['lift']:+.1%} | {net:+.1%} | {r['t']:+.2f} | {r['pos_share']:.0%} |"

    hdr = ["| 群 | 期間 | 観測数 | 基準日数 | 差(日付ごと) | コスト後 | t値 | プラスの日 |", "|---|---|---|---|---|---|---|---|"]
    print("\n【事前登録のセル】")
    print("\n".join(hdr + [row("A(資産バリュー)", obs["hit_A"], 120), row("B(シクリカル底)", obs["hit_B"], 250)]))
    print("\n【探索:全期間 × 群】")
    lines = list(hdr)
    nn = obs["netnet"].notna() & (obs["netnet"] < th.netnet_max)
    pbr_eq = (obs["pbr"] < th.asset_pbr_max) & (obs["equity_ratio"] >= th.asset_equity_ratio_min)
    for name, m in (("A全体", obs["hit_A"]), ("A のうちネットネット指数<1", nn), ("A のうち PBR<0.5 かつ 自己資本比率60%以上", pbr_eq),
                    ("B", obs["hit_B"]), ("C(参考:四半期が無いので通常は該当しない)", obs["hit_C"]),
                    ("型が2つ以上", obs["n_types"] >= 2), ("ABCのいずれか", obs["hit_A"] | obs["hit_B"] | obs["hit_C"])):
        for h in (20, 60, 120, 250):
            lines.append(row(name, m, h))
    print("\n".join(lines))
    print("\n型Aの該当数:", int(obs["hit_A"].sum()), "型Bの該当数:", int(obs["hit_B"].sum()))
    # 前半・後半(各約19日)
    mid = sorted(obs["asof"].unique())[len(obs["asof"].unique()) // 2]
    print("\n【前半・後半(各約19基準日。基準日数不足につき参考)】")
    for pn, sub in (("前半", obs[obs["asof"] < mid]), ("後半", obs[obs["asof"] >= mid])):
        for name, col, h in (("A", "hit_A", 120), ("B", "hit_B", 250)):
            m = sub[col]
            r = bt.date_paired_lift(sub, m, f"excess_final_{h}")
            print(f"{pn} {name} {h}d: 観測{int((m & sub[f'excess_final_{h}'].notna()).sum())} 基準日{r['n_dates']} 差{r['lift']:+.1%} t={r['t']:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
