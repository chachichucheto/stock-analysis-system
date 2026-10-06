"""過去検証の検出力の分析(docs/VALUE_VALIDATION.md)。

「ある型に本物の優位性(20営業日で TOPIX 超過 +e%)があるとき、過去検証が何割の確率で見つけられるか」と、
「優位性が無いのに、有意と出てしまう確率(偽陽性)」を、合成データで測る。実データは使わない。

  python scripts/value_power_analysis.py [--trials 200]

前提(すべて仮置き。実際の小型株の値動きはもっと裾が厚いので、現実の検出力はこれより低い方向):
  - 母集団 M=300銘柄/日、型に該当するのは S=10銘柄/日(無作為)
  - 銘柄固有の日次ボラティリティ 2.0%、その日の全銘柄に共通の地合い(小型株の対TOPIX)の20日ボラティリティ 4%
  - 「動いた」= 20営業日のどこかで累積の超過が +10% 以上
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assoc.value.backtest import date_paired_lift, perm_p_value  # noqa: E402


def simulate(D: int, M: int, S: int, edge20: float, rng: np.random.Generator,
             sig_idio_d: float = 0.02, sig_common_20: float = 0.04, steps: int = 20) -> pd.DataFrame:
    common = rng.normal(0, sig_common_20, size=(D, 1, 1)) / steps
    idio = rng.normal(0, sig_idio_d, size=(D, M, steps))
    drift = np.zeros((D, M, 1))
    drift[:, :S, :] = edge20 / steps
    path = np.cumsum(common + idio + drift, axis=2)
    moved = path.max(axis=2) >= 0.10
    final = path[:, :, -1]
    return pd.DataFrame({"asof": np.repeat(np.arange(D), M), "moved": moved.ravel(),
                         "excess_final": final.ravel(), "sel": np.tile(np.arange(M) < S, D)})


def simulate_regime(D: int, M: int, rng: np.random.Generator, rebound: float = 0.03,
                    sig_idio_d: float = 0.02, sig_common_20: float = 0.04, steps: int = 20) -> pd.DataFrame:
    """型B・Dのように、**相場が売られた日に該当が集中する**世界。優位性は無いが、その後の地合いが戻る(+rebound)。

    売られた日(全体の20%)は40銘柄が該当、それ以外の日は2銘柄だけ該当。該当銘柄に固有の優位性はゼロ。
    日付をまたいで混ぜる検定は、これを「優位性」と取り違える。
    """
    crash = rng.random(D) < 0.2
    common = (rng.normal(0, sig_common_20, size=(D, 1, 1)) + np.where(crash, rebound, 0.0)[:, None, None]) / steps
    idio = rng.normal(0, sig_idio_d, size=(D, M, steps))
    path = np.cumsum(common + idio, axis=2)
    sel = np.zeros((D, M), dtype=bool)
    for d in range(D):
        sel[d, : 40 if crash[d] else 2] = True
    return pd.DataFrame({"asof": np.repeat(np.arange(D), M), "moved": (path.max(axis=2) >= 0.10).ravel(),
                         "excess_final": path[:, :, -1].ravel(), "sel": sel.ravel()})


def one(D, M, S, edge, rng, draws, regime=False):
    obs = simulate_regime(D, M, rng) if regime else simulate(D, M, S, edge, rng)
    mask = obs["sel"]
    seed = int(rng.integers(1 << 31))
    p_strat = perm_p_value(obs, mask, draws, seed, stratified=True)
    p_naive = perm_p_value(obs, mask, draws, seed, stratified=False)
    t = date_paired_lift(obs, mask, "excess_final")["t"]
    rate_up = obs.loc[mask, "moved"].mean() > obs["moved"].mean()
    return (p_strat < 0.05 and rate_up, p_naive < 0.05 and rate_up, t > 2.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    M, S = 300, 10
    print(f"母集団{M}銘柄/日・該当{S}銘柄/日・各条件{a.trials}回。値は「有意と判定した割合」")
    print("edge=0 の行が偽陽性率(5%が理想)。edge>0 の行が検出力(高いほど良い)\n")
    print(f"{'20日超過の優位性':>14} {'基準日数':>6} | {'層別の並べ替え':>12} {'層別なし':>9} {'日付ごとのt検定':>12}")
    for edge in (0.0, 0.01, 0.02, 0.04, 0.08):
        for D in (12, 24, 60):
            res = np.array([one(D, M, S, edge, rng, a.draws) for _ in range(a.trials)]).mean(axis=0)
            print(f"{edge:>13.0%} {D:>8} | {res[0]:>12.1%} {res[1]:>9.1%} {res[2]:>12.1%}")
        print()
    print("【売られた相場に該当が集中する世界(該当銘柄に優位性はゼロ)】 → 本来は5%程度が理想")
    print(f"{'基準日数':>6} | {'層別の並べ替え':>12} {'層別なし':>9} {'日付ごとのt検定':>12}")
    for D in (24, 60, 120):
        res = np.array([one(D, M, S, 0.0, rng, a.draws, regime=True) for _ in range(a.trials)]).mean(axis=0)
        print(f"{D:>8} | {res[0]:>12.1%} {res[1]:>9.1%} {res[2]:>12.1%}")


if __name__ == "__main__":
    main()
