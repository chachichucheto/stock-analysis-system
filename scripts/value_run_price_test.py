"""型D(25日線からの下方乖離の逆張り)の、株価だけの過去検証(docs/VALUE_VALIDATION.md §6 順1)。

  python scripts/value_fetch_prices.py --universe data/value/data_j.xlsx --out data/prices   # 先に株価を取る
  python scripts/value_run_price_test.py --universe data/value/data_j.xlsx --prices data/prices

**事前登録(結果を見る前に固定。変えるときは理由と日付を VALUE_VALIDATION.md に残す)**
  - 主な仮説 H4:型D(業種別の閾値以下の下方乖離)は、20営業日の TOPIX 超過リターンが、同じ日のベースラインより高い
  - 合格:前半(2016-01〜2020-12)・後半(2021-01〜2025-09)の**両方**で、日付ごとの差が正かつ t>2
  - 対象:国内株のうち、TOPIX の規模区分が Small1・Small2・その他(Core30・Large70・Mid400 を除く)。
    売買代金(20日平均)3,000万円以上、株価100円以上。基準日は月末の最終営業日
  - 取引コスト:往復 1.0%(スプレッド+手数料の仮置き。小型株の逆張りは板が薄い)を差し引いた値も出す
  - それ以外の分析(閾値の感度、60営業日、業種別、流動性別)は**探索**。採用の根拠にしない
  - 生存者バイアス:Yahoo には上場廃止銘柄が無い。売られた銘柄ほど上場廃止になりやすいので、型Dの成績は楽観側に偏る。
    **この検証で型Dが勝てなければ、決定的に勝てない。勝てても、確定ではない**
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assoc.market.prices import CsvDirPriceSource  # noqa: E402
from assoc.value import backtest as bt  # noqa: E402
from assoc.value.data import Universe  # noqa: E402
from assoc.value.models import UniverseRow  # noqa: E402
from assoc.value.thresholds import ValueThresholds  # noqa: E402

DOMESTIC = ("プライム（内国株式）", "スタンダード（内国株式）", "グロース（内国株式）")
SMALL_CLASSES = ("TOPIX Small 1", "TOPIX Small 2", "-")
PERIODS = {"前半": (date(2016, 1, 1), date(2020, 12, 31)), "後半": (date(2021, 1, 1), date(2025, 9, 30))}
ROUND_TRIP_COST = 0.010


def load_universe(path: Path) -> tuple[Universe, set[str]]:
    df = pd.read_excel(path)
    df = df[df["市場・商品区分"].isin(DOMESTIC)]
    rows = [UniverseRow(code=str(r["コード"]).strip(), name=str(r["銘柄名"]), market=r["市場・商品区分"],
                        sector33=str(r["33業種区分"]), listed_date=None) for _, r in df.iterrows()]
    small = {str(r["コード"]).strip() for _, r in df.iterrows() if str(r["規模区分"]).strip() in SMALL_CLASSES}
    return Universe(rows), small


def fmt(x, pct=True):
    return "—" if x != x else (f"{x:.1%}" if pct else f"{x:.2f}")


def table_rows(obs: pd.DataFrame, masks: dict[str, pd.Series], col: str) -> list[str]:
    out = []
    for name, m in masks.items():
        r = bt.date_paired_lift(obs, m, col)
        n = int((m & obs[col].notna()).sum())
        net = r["lift"] - ROUND_TRIP_COST if r["lift"] == r["lift"] else float("nan")
        out.append(f"| {name} | {n} | {r['n_dates']} | {fmt(r['lift'])} | {fmt(net)} | {fmt(r['t'], False)} | {fmt(r['pos_share'])} |")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--prices", required=True)
    ap.add_argument("--out", default="data/reports/value_price_test_D.md")
    a = ap.parse_args()

    uni, small = load_universe(Path(a.universe))
    px = CsvDirPriceSource(Path(a.prices))
    topix = px.daily("1306", date(2013, 1, 1), date(2026, 12, 31))
    th = ValueThresholds()
    lines = ["# 型D(乖離率の逆張り)の株価だけの過去検証", "",
             "- 事前登録は `scripts/value_run_price_test.py` の冒頭。主な仮説 H4 = 型D × 20営業日。合格 = 前半・後半の両方で、日付ごとの差が正かつ t>2。",
             f"- 対象:国内株のうち規模区分が Small1・Small2・その他({len(small)}銘柄)。売買代金 {th.min_turnover_yen / 1e4:,.0f}万円以上。月末の基準日。",
             f"- 取引コストの仮定:往復 {ROUND_TRIP_COST:.1%}。「差(コスト後)」= 差 − コスト。",
             "- **生存者バイアス:上場廃止銘柄が無い(Yahoo)。型Dは楽観側に偏る。**", ""]
    primary = {}
    all_obs = []
    for name, (s, e) in PERIODS.items():
        dates = bt.month_end_dates(s, e)
        res = bt.run_price_backtest(uni, px, topix, dates, th, size_ok=lambda c: c in small)
        res.obs.to_csv(f"data/reports/value_price_test_obs_{name}.csv", index=False, encoding="utf-8-sig")
        o = res.obs.assign(period=name)
        all_obs.append(o)
        d = o["hit_D"]
        r20 = bt.date_paired_lift(o, d, "excess_final_20")
        p_moved = bt.perm_p_value(o, d, 5000, th.seed)
        primary[name] = (r20, p_moved, int(d.sum()), o["asof"].nunique(), res.warnings)
        print(name, "観測", len(o), "型D", int(d.sum()), r20, p_moved, flush=True)

    lines += ["## 主な仮説 H4(型D × 20営業日)", "",
              "| 期間 | 型Dの観測数 | 基準日数 | 日付ごとの差 | 差(コスト後) | t値 | 差がプラスだった日の割合 | 動いた割合の層別p値 |",
              "|---|---|---|---|---|---|---|---|"]
    ok = True
    for name, (r, p, n, nd, _) in primary.items():
        net = r["lift"] - ROUND_TRIP_COST
        lines.append(f"| {name} | {n} | {r['n_dates']} | {fmt(r['lift'])} | {fmt(net)} | {fmt(r['t'], False)} | {fmt(r['pos_share'])} | {p:.3f} |")
        ok &= bool(r["lift"] > 0 and r["t"] > 2)
    lines += ["", f"**判定(H4):{'合格(両期間で差が正かつ t>2)。ただし生存者バイアスがあるので確定ではない' if ok else '不合格(少なくとも一方の期間で、差が正かつ t>2 を満たさない)'}**", ""]
    cost_ok = all(r["lift"] - ROUND_TRIP_COST > 0 for r, *_ in primary.values())
    lines += [f"取引コスト({ROUND_TRIP_COST:.1%})を引くと、両期間とも差が正か:{'はい' if cost_ok else 'いいえ'}", ""]
    for name, (_, _, _, _, warns) in primary.items():
        for w in warns:
            lines.append(f"- ⚠ [{name}] {w}")
    lines.append("")

    obs = pd.concat(all_obs, ignore_index=True)
    lines += ["## 探索(採用の根拠にしない)", "",
              "各行は「該当群 − その日のベースライン」の日付ごとの平均。多くの行を見れば、偶然でも t>2 が出る。", ""]
    hdr = "| 群 | 観測数 | 基準日数 | 差 | 差(コスト後) | t値 | プラスの日の割合 |\n|---|---|---|---|---|---|---|"
    for h in th.horizons:
        lines += [f"### 期間 {h}営業日(型Dの全期間・両期間込み)", "", hdr]
        masks = {"型D(業種別の閾値)": obs["hit_D"]}
        for thr in (-0.15, -0.20, -0.25, -0.30, -0.40):
            masks[f"乖離 ≤ {thr:.0%}(全業種共通)"] = obs["deviation"] <= thr
        lines += table_rows(obs, masks, f"excess_final_{h}") + [""]
    lines += ["### 期間別の内訳(20営業日、型D)", "", hdr]
    masks = {f"{p}": obs["hit_D"] & (obs["period"] == p) for p in PERIODS}
    for q, lab in ((0.0, "売買代金 下位1/3"), (1 / 3, "売買代金 中位1/3"), (2 / 3, "売買代金 上位1/3")):
        lo, hi = obs["turnover"].quantile(q), obs["turnover"].quantile(min(q + 1 / 3, 1.0))
        masks[lab] = obs["hit_D"] & (obs["turnover"] >= lo) & (obs["turnover"] <= hi)
    lines += table_rows(obs, masks, "excess_final_20") + [""]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"レポート: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
