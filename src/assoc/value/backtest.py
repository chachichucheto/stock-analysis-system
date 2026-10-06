"""過去検証(docs/VALUE_DESIGN.md §9)。「有名投資家の手法だから勝てる」と仮定せず、データで確かめる。

小型株の検証は、次の偏りで成績が大きく水増しされる。ここでは構造で防ぎ、防げないものは警告に出す。

| 偏り | 防ぎ方 |
|---|---|
| 先読み(決算の開示前の数字を使う) | 財務は disclosed_date < asof のものだけ(data.FinancialsStore)。株価も asof 以前だけで Snapshot を作る |
| 生存者バイアス(上場廃止が消える) | 銘柄マスタに上場廃止銘柄が無ければ警告(require_delisted=True ならエラー)。打ち切られた観測は最後の終値で評価して数える |
| 約定できない価格 | 売買代金の下限を満たす銘柄だけを対象にする(snapshot.passes_base) |
| 比較対象の取り違え | 比べる相手は「同じ日・同じ足切りを通った銘柄すべて」(ベースライン)。TOPIX との比較ではなく、型の効果だけを見る |
| 同じ日の観測は独立でない | 日付の数(n_dates)を併記する。結論は日付の数が十分なときだけ |
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from assoc.market.prices import PriceSource
from assoc.timeutil import add_business_days, is_business_day
from assoc.value.dangers import detect_dangers
from assoc.value.data import FinancialsStore, Universe
from assoc.value.score import count_types
from assoc.value.screens import run_screens
from assoc.value.snapshot import build_snapshot, passes_base
from assoc.value.thresholds import ValueThresholds

GROUPS = ("A", "B", "C", "D", "ABCのいずれか", "型が2つ以上", "型が1つだけ")


@dataclass
class BacktestResult:
    obs: pd.DataFrame
    summary: pd.DataFrame
    warnings: list[str] = field(default_factory=list)


def month_end_dates(start: date, end: date) -> list[date]:
    """各月の最終営業日(start〜end)。検証の基準日に使う。"""
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        d = nxt - timedelta(days=1)
        while not is_business_day(d):
            d -= timedelta(days=1)
        if start <= d <= end:
            out.append(d)
        y, m = nxt.year, nxt.month
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def _forward(stock: pd.DataFrame, topix_ser: pd.Series, asof: date, entry: float, end_h: date) -> dict | None:
    fwd = stock[(stock["date"] > asof) & (stock["date"] <= end_h)]
    if fwd.empty:
        return None
    t0 = float(topix_ser.loc[:asof].iloc[-1])
    ex = []
    for d, c in zip(fwd["date"], fwd["close"]):
        t = float(topix_ser.loc[:d].iloc[-1])
        ex.append((c / entry - 1.0) - (t / t0 - 1.0))
    last = fwd.iloc[-1]["date"]
    gap = sum(1 for k in range(1, (end_h - last).days + 1) if is_business_day(last + timedelta(days=k)))
    return {"excess_max": max(ex), "excess_final": ex[-1], "truncated": gap > 3, "last_date": last}


def run_backtest(universe: Universe, fin: FinancialsStore, prices: PriceSource, topix: pd.DataFrame,
                 dates: list[date], th: ValueThresholds, require_delisted: bool = False) -> BacktestResult:
    warnings: list[str] = []
    if not universe.has_delisted():
        msg = ("銘柄マスタに上場廃止の銘柄がありません。生存者バイアスで成績が水増しされている恐れがあります"
               "(上場廃止銘柄とその株価を入れてください)")
        if require_delisted:
            raise ValueError(msg)
        warnings.append(msg)
    if not dates:
        raise ValueError("検証の基準日がありません")
    topix_ser = pd.Series(topix["close"].astype(float).values, index=topix["date"].values).sort_index()
    lo, hi = min(dates) - timedelta(days=1900), max(dates) + timedelta(days=90)
    cache: dict[str, pd.DataFrame] = {}

    def load(code: str) -> pd.DataFrame:
        if code not in cache:
            cache[code] = prices.daily(code, lo, hi)
        return cache[code]

    rows, no_forward, skipped_dates = [], 0, []
    for asof in dates:
        if asof not in topix_ser.index:
            skipped_dates.append(asof)
            continue
        end_h = add_business_days(asof, th.horizon_days)
        for u in universe.members(asof):
            df = load(u.code)
            if df.empty:
                continue
            snap = build_snapshot(u, asof, fin, df, th)
            if snap is None:
                continue
            # 先読みの最終確認(構造で防いでいるが、壊れたら即座に気づくよう検査する)
            assert snap.prices["date"].max() <= asof
            assert snap.fin is None or snap.fin.disclosed_date < asof
            ok, _ = passes_base(snap, th)
            if not ok:
                continue
            dangers = detect_dangers(snap, th, master=None)   # 監理・継続疑義は「現在の状態」なので使わない
            if any(d.severity == "block" for d in dangers):
                continue
            screens = run_screens(snap, th)
            fwd = _forward(df, topix_ser, asof, snap.price, end_h)
            if fwd is None:
                no_forward += 1
                continue
            counted = count_types(screens)
            rows.append({"asof": asof, "code": u.code, "sector33": u.sector33, "mcap": snap.mcap,
                         "hit_A": screens["A"].hit, "hit_B": screens["B"].hit, "hit_C": screens["C"].hit,
                         "hit_D": screens["D"].hit, "n_types": len(counted), "types": "".join(counted),
                         "moved": fwd["excess_max"] >= th.moved_excess, "excess_max": fwd["excess_max"],
                         "excess_final": fwd["excess_final"], "truncated": fwd["truncated"]})
    obs = pd.DataFrame(rows)
    if skipped_dates:
        warnings.append(f"TOPIX の株価が無く、検証から外した基準日: {len(skipped_dates)}日")
    if no_forward:
        warnings.append(f"基準日の翌日以降の株価が無く外した観測: {no_forward}件(上場廃止の直前など。外れた分だけ成績が楽観的になる)")
    if obs.empty:
        warnings.append("観測が1件もありません(足切りが厳しすぎる、またはデータが足りません)")
        return BacktestResult(obs, pd.DataFrame(), warnings)
    trunc = int(obs["truncated"].sum())
    if trunc:
        warnings.append(f"株価が途中で途切れた観測 {trunc}件は、最後の終値で評価しています"
                        "(上場廃止の実際の清算値より楽観的な可能性)")
    return BacktestResult(obs, summarize(obs, th), warnings)


def _mask(obs: pd.DataFrame, group: str) -> pd.Series:
    if group in ("A", "B", "C", "D"):
        return obs[f"hit_{group}"]
    abc = obs["hit_A"] | obs["hit_B"] | obs["hit_C"]
    if group == "ABCのいずれか":
        return abc
    if group == "型が2つ以上":
        return obs["n_types"] >= 2
    if group == "型が1つだけ":
        return obs["n_types"] == 1
    raise ValueError(group)


def summarize(obs: pd.DataFrame, th: ValueThresholds) -> pd.DataFrame:
    """型ごとの「動いた」割合を、ベースライン(同じ足切りを通った全銘柄)と比べる。"""
    base_moved = obs["moved"].tolist()
    base_rate = sum(base_moved) / len(base_moved)
    rng = random.Random(th.seed)
    out = [{"group": "ベースライン(全体)", "n": len(obs), "n_dates": obs["asof"].nunique(),
            "moved_rate": base_rate, "ci_low": wilson(sum(base_moved), len(base_moved))[0],
            "ci_high": wilson(sum(base_moved), len(base_moved))[1], "mean_excess": obs["excess_final"].mean(),
            "median_excess": obs["excess_final"].median(), "baseline_rate": base_rate, "lift": 0.0,
            "p_value": float("nan"), "verdict": "—"}]
    for g in GROUPS:
        sub = obs[_mask(obs, g)]
        n = len(sub)
        if n == 0:
            out.append({"group": g, "n": 0, "n_dates": 0, "moved_rate": float("nan"), "ci_low": float("nan"),
                        "ci_high": float("nan"), "mean_excess": float("nan"), "median_excess": float("nan"),
                        "baseline_rate": base_rate, "lift": float("nan"), "p_value": float("nan"), "verdict": "該当なし"})
            continue
        k = int(sub["moved"].sum())
        rate = k / n
        lo, hi = wilson(k, n)
        p = float("nan")
        if n < len(base_moved):
            ge = sum(1 for _ in range(th.permutation_draws)
                     if sum(rng.sample(base_moved, n)) / n >= rate)
            p = (ge + 1) / (th.permutation_draws + 1)
        if n < th.min_samples:
            verdict = "サンプル不足(結論を出さない)"
        elif p == p and p < 0.05 and rate > base_rate:
            verdict = "ベースラインより高い(要・別期間での再確認)"
        else:
            verdict = "ベースラインと差があるとは言えない"
        out.append({"group": g, "n": n, "n_dates": sub["asof"].nunique(), "moved_rate": rate, "ci_low": lo,
                    "ci_high": hi, "mean_excess": sub["excess_final"].mean(), "median_excess": sub["excess_final"].median(),
                    "baseline_rate": base_rate, "lift": rate - base_rate, "p_value": p, "verdict": verdict})
    return pd.DataFrame(out)


def render_markdown(res: BacktestResult, th: ValueThresholds, start: date, end: date) -> str:
    lines = [f"# 割安カタリスト・モデル 過去検証 {start}〜{end}", "",
             f"- 「動いた」= 基準日の終値から最長{th.horizon_days}営業日の間に、TOPIX を {th.moved_excess:.0%} 以上上回った",
             f"- 比べる相手 = 同じ日・同じ足切り(時価総額・売買代金・危険信号)を通った全銘柄(ベースライン)",
             f"- 観測 {len(res.obs)}件 / 基準日 {res.obs['asof'].nunique() if not res.obs.empty else 0}日", ""]
    if res.warnings:
        lines += ["## 警告(結果の読み方に関わる)", ""] + [f"- ⚠ {w}" for w in res.warnings] + [""]
    if not res.summary.empty:
        lines += ["## 結果", "",
                  "| 群 | 観測数 | 基準日数 | 動いた割合 | 95%区間 | ベースライン | 差 | p値 | TOPIX超過(平均/中央) | 判定 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in res.summary.itertuples():
            f = lambda x, pct=True: "—" if x != x else (f"{x:.1%}" if pct else f"{x:.3f}")
            lines.append(f"| {r.group} | {r.n} | {r.n_dates} | {f(r.moved_rate)} | {f(r.ci_low)}〜{f(r.ci_high)} | "
                         f"{f(r.baseline_rate)} | {f(r.lift)} | {f(r.p_value, False)} | "
                         f"{f(r.mean_excess)}/{f(r.median_excess)} | {r.verdict} |")
        lines += ["", "## 読み方", "",
                  "- p値は「ベースラインから同じ数をランダムに選んだとき、これ以上の割合になる確率」(片側)。",
                  "- 同じ日の銘柄は値動きが連動するので、観測は独立ではない。**基準日数が少ないうちは p値を信用しない。**",
                  "- 「ベースラインより高い」と出ても、別の期間・別の相場で再確認するまで採用しない。",
                  "- 型が重なったほうが成績が良いか(「型が2つ以上」と「型が1つだけ」の比較)が、合議スコアの存在理由。"
                  "差が出なければ、この設計は見直す。"]
    return "\n".join(lines) + "\n"
