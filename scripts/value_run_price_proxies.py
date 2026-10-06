"""型B・型Cの「株価だけの代用」の検証(財務データなし)。docs/VALUE_VALIDATION.md §7.4。

**事前登録(結果を見る前に固定)** 代用は本物の型B・Cではない。財務の条件(赤字・生存性、利益の加速・PER)が無い。
  - Bp(型Bの代用):5年高値から -2/3 以下に下落(履歴1,000営業日以上)。主なセル = 250営業日
  - Cp(型Cの代用):直近10日に出来高が直前20日平均の2倍以上の日があり、かつ 60営業日の騰落が 0〜+50%、かつ 25日線より上。主なセル = 60営業日
  - 合格:前半・後半の両方で、日付ごとの差が正かつ t>2(型Dと同じ)
  - 型D・Bp・Cp の3つを見るので、1つが t>2 でも偶然の恐れがある(多重比較)。両期間の合格を必須にしてある
  - 対象・コスト・生存者バイアスは scripts/value_run_price_test.py と同じ
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
from assoc.value.thresholds import ValueThresholds  # noqa: E402
import value_run_price_test as base  # noqa: E402

COST = 0.010


def main() -> int:
    uni, small = base.load_universe(Path("data/value/data_j.xlsx"))
    px = CsvDirPriceSource(Path("data/prices"))
    topix = px.daily("1306", date(2013, 1, 1), date(2026, 12, 31))
    th = ValueThresholds()
    out = Path("data/reports/value_price_proxies_obs.csv")
    if out.exists():
        obs = pd.read_csv(out, low_memory=False, parse_dates=False)
        obs["asof"] = pd.to_datetime(obs["asof"]).dt.date
    else:
        dates = bt.month_end_dates(date(2016, 1, 1), date(2025, 9, 30))
        res = bt.run_price_backtest(uni, px, topix, dates, th, size_ok=lambda c: c in small)
        obs = res.obs
        obs.to_csv(out, index=False, encoding="utf-8-sig")
    half = {"前半": (date(2016, 1, 1), date(2020, 12, 31)), "後半": (date(2021, 1, 1), date(2025, 9, 30))}
    masks = {
        "型D(参考:前回の不合格)": (obs["hit_D"], 20),
        "Bp 5年高値から-2/3以下": ((obs["drawdown_5y"] <= -2 / 3) & (obs["bars"] >= 1000), 250),
        "Cp 出来高急増×初動×25日線より上": (obs["vol_surge"].fillna(False).astype(bool) & obs["ret_60"].between(0.0, 0.5)
                                     & (obs["deviation"] >= 0), 60),
    }
    lines = ["| 代用 | 主な期間 | 期間 | 観測数 | 基準日数 | 差(日付ごと) | コスト後 | t値 | プラスの日 |", "|---|---|---|---|---|---|---|---|---|"]
    verdicts = {}
    for name, (m, h) in masks.items():
        ok = True
        for pn, (s, e) in half.items():
            sub = obs[(obs["asof"] >= s) & (obs["asof"] <= e)]
            mm = m.loc[sub.index]
            r = bt.date_paired_lift(sub, mm, f"excess_final_{h}")
            n = int((mm & sub[f"excess_final_{h}"].notna()).sum())
            net = r["lift"] - COST
            lines.append(f"| {name} | {h}営業日 | {pn} | {n} | {r['n_dates']} | {r['lift']:+.1%} | {net:+.1%} | {r['t']:+.2f} | {r['pos_share']:.0%} |")
            ok &= bool(r["lift"] > 0 and r["t"] > 2)
        verdicts[name] = ok
    print("\n".join(lines))
    print()
    for k, v in verdicts.items():
        print(f"{k}: {'合格' if v else '不合格'}")
    # 探索:他の期間も
    print("\n【探索】全期間込みの日付ごとの差")
    for name, (m, _) in masks.items():
        row = []
        for h in th.horizons:
            r = bt.date_paired_lift(obs, m, f"excess_final_{h}")
            row.append(f"{h}d {r['lift']:+.1%}(t={r['t']:+.1f})")
        print(f"{name}: n={int(m.sum())} | " + " | ".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
