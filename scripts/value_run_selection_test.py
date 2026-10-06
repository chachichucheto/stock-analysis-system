"""型Aの「中から選ぶ」ルールの検証(docs/VALUE_VALIDATION.md §7.7)。**指数(TOPIX)を上回るか**を見る。

前回までの検証は、型Aに該当した銘柄を**全部均等に買った**場合の成績だった。それは TOPIX 比でほぼ互角(平均+2.2%、中央値-1.7%)で、
個別の選定をしないと、指数を明確には上回れない。ここでは、型Aの中から選ぶルールで改善するかを見る。

**事前登録(結果を見る前に固定)** 入力は `scripts/value_run_fin_test.py` が作る obs(Yahoo 年次財務、2022-07〜2025-09)。
  - ルール(型Aの中から選ぶ。投資家の手法に由来する、事前に決めた6つだけ):
      S0 型A全体(基準)
      S1 営業利益(12か月)が黒字                            ← 「潰れない・稼ぐ力がある」(たーちゃん氏・かぶ1000氏)
      S2 発行済株式数の前年比が +2% 以下(希薄化なし)           ← 危険信号(希薄化)の除外
      S3 直近10日に出来高が2倍以上の日がある                   ← カタリスト・市場が気づき始めた兆し(片山氏・ぱりてきさす氏)
      S4 S1 かつ S2
      S5 ネットネット指数<1 かつ S1
      S6 各基準日に、S4 を満たす銘柄のうちネットネット指数が低い順に上位10銘柄(集中投資の想定)
  - 主な指標:**TOPIX 超過(TOPIX との差)**の、日付ごとの平均。主な期間 = 120営業日(H1' と同じ)
  - 比べる相手:(a) TOPIX そのもの(超過が0)、(b) 型A全体(S0)
  - t値は、重なり(月次の基準日で120営業日の窓は約6倍重なる)を補正したニューイ・ウェスト(遅れ=期間/21か月)
  - 6ルールを見るので多重比較。**1つが良く見えても採用しない。S0 との差が正で、補正後 t>2 かつ両半期で正のものだけ、「有望」と呼ぶ**
  - 限界はすべて `value_run_fin_test.py` と同じ(Yahoo 財務、約3年、上場廃止なし)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

COST = 0.010


def nw_t(x: pd.Series, lag: int) -> float:
    x = x.dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 4:
        return float("nan")
    m = x.mean()
    e = x - m
    s = (e * e).sum() / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * (e[k:] * e[:-k]).sum() / n
    return float(m / math.sqrt(s / n)) if s > 0 else float("inf")


def date_level(o: pd.DataFrame, mask: pd.Series, col: str) -> pd.Series:
    return o[mask & o[col].notna()].groupby("asof")[col].mean().sort_index()


def evaluate(o: pd.DataFrame, masks: dict[str, pd.Series], h: int, ref: str = "S0 型A全体") -> list[str]:
    col = f"excess_final_{h}"
    lag = max(1, math.ceil(h / 21) - 1)
    mid = sorted(o["asof"].unique())[len(o["asof"].unique()) // 2]
    ref_series = date_level(o, masks[ref], col)
    lines = []
    for name, m in masks.items():
        sub = o[m & o[col].notna()]
        s = date_level(o, m, col)
        if len(s) < 4:
            lines.append(f"| {name} | {len(sub)} | {sub['code'].nunique()} | {len(s)} | — | — | — | — | — | — |")
            continue
        diff = (s - ref_series.reindex(s.index)).dropna()
        first, second = s[s.index < mid], s[s.index >= mid]
        lines.append(
            f"| {name} | {len(sub)} | {sub['code'].nunique()} | {len(s)} | {s.mean():+.1%} | {nw_t(s, lag):+.1f} | "
            f"{sub[col].median():+.1%} | {(sub[col] > 0).mean():.0%} | "
            f"{"—" if name == ref else f'{diff.mean():+.1%} (t={nw_t(diff, lag):+.1f})'} | "
            f"{first.mean():+.1%} / {second.mean():+.1%} |")
    return lines


def main() -> int:
    o = pd.read_csv("data/reports/value_fin_test_obs.csv", low_memory=False)
    o["asof"] = pd.to_datetime(o["asof"]).dt.date
    a = o["hit_A"].astype(bool)
    s1 = a & (o["oi_ttm"] > 0)
    s2 = a & (o["dilution"] <= 0.02)
    s3 = a & o["vol_surge"].fillna(False).astype(bool)
    s4 = s1 & s2
    s5 = a & (o["netnet"] < 1) & (o["oi_ttm"] > 0)
    # S6:各基準日に、S4 を満たす銘柄のうちネットネット指数が低い順に上位10銘柄
    s6 = pd.Series(False, index=o.index)
    cand = o[s4 & o["netnet"].notna()]
    for _, g in cand.groupby("asof"):
        s6.loc[g.sort_values(["netnet", "code"]).head(10).index] = True
    masks = {"S0 型A全体": a, "S1 黒字": s1, "S2 希薄化なし": s2, "S3 出来高の兆し": s3, "S4 黒字かつ希薄化なし": s4,
             "S5 ネットネット<1かつ黒字": s5, "S6 S4の中でネットネット指数が低い上位10": s6}
    out = ["# 型Aの中から選ぶルールの検証(TOPIX 超過)", "",
           "- 事前登録は `scripts/value_run_selection_test.py` の冒頭。TOPIX 超過 = 銘柄の騰落率 − TOPIX の騰落率。",
           "- 「基準日ごとの平均」を平均した値。t値は重なりを補正(ニューイ・ウェスト)。S0 との差も同様。",
           "- **Yahoo 財務、約3年、上場廃止なし。参考値。**", ""]
    hdr = ("| ルール | 観測数 | 銘柄数 | 基準日数 | TOPIX超過(基準日平均) | 補正t | 中央値 | TOPIX超えの割合 | S0との差 | 前半 / 後半 |\n"
           "|---|---|---|---|---|---|---|---|---|---|")
    for h in (60, 120, 250):
        out += [f"## {h}営業日", "", hdr] + evaluate(o, masks, h) + [""]
    # 参考:足切りを通った全体(TOPIX 超過)
    col = "excess_final_120"
    allm = pd.Series(True, index=o.index)
    s = date_level(o, allm, col)
    out += ["## 参考:足切りを通った小型株すべて(120営業日)", "",
            f"- TOPIX 超過 {s.mean():+.1%}、補正t {nw_t(s, 5):+.1f}、中央値 {o[col].median():+.1%}、TOPIX超えの割合 {(o[col] > 0).mean():.0%}", ""]
    text = "\n".join(out)
    Path("data/reports/value_selection_test.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
