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
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from assoc.market.prices import PriceSource
from assoc.timeutil import add_business_days, is_business_day
from assoc.value import metrics
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
    horizons: pd.DataFrame = field(default_factory=pd.DataFrame)


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


def horizon_ends(asof: date, hs: tuple[int, ...]) -> dict[int, date]:
    """基準日から h 営業日後の日付(営業日の計算は遅いので、基準日ごとに1回だけ計算して使い回す)。"""
    return {h: add_business_days(asof, h) for h in hs}


def _forward(stock: pd.DataFrame, topix_ser: pd.Series, asof: date, entry: float, hs: tuple[int, ...],
             primary: int, delisted: date | None, ends: dict[int, date] | None = None) -> dict | None:
    """基準日の終値から、各期間 h(営業日)までの TOPIX 超過を計算する。

    - 期間の終わりが TOPIX の最終日を超える → その期間は未確定(NaN)。直近の基準日で、長い期間が空くのは正常
    - 株価が期間の途中で途切れた → 上場廃止なら最後の終値で評価して数える(truncated)。そうでなければ未確定(NaN)
    - 主な期間(primary)が未確定なら None(観測にしない)
    """
    t0 = float(topix_ser.loc[:asof].iloc[-1])
    topix_last = topix_ser.index[-1]
    ends = ends or horizon_ends(asof, hs)
    fwd_all = stock[(stock["date"] > asof) & (stock["date"] <= ends[max(hs)])]
    if fwd_all.empty:
        return None
    t = topix_ser.reindex(fwd_all["date"].tolist(), method="ffill").to_numpy(dtype=float)
    ex_all = (fwd_all["close"].to_numpy(dtype=float) / entry - 1.0) - (t / t0 - 1.0)
    dates = fwd_all["date"].tolist()
    out: dict = {}
    for h in hs:
        end_h = ends[h]
        if end_h > topix_last:
            out[h] = None
            continue
        idx = [i for i, d in enumerate(dates) if d <= end_h]
        if not idx:
            out[h] = None
            continue
        last = dates[idx[-1]]
        gap = sum(1 for k in range(1, (end_h - last).days + 1) if is_business_day(last + timedelta(days=k)))
        truncated = gap > 3
        if truncated and not (delisted is not None and delisted <= end_h + timedelta(days=7)):
            out[h] = None                                  # 上場廃止ではないのに株価が無い=データの欠け
            continue
        sub = ex_all[: idx[-1] + 1]
        out[h] = {"excess_max": float(sub.max()), "excess_final": float(sub[-1]), "truncated": truncated}
    return out if out.get(primary) is not None else None


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
        ends = horizon_ends(asof, th.horizons)
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
            fwd = _forward(df, topix_ser, asof, snap.price, th.horizons, th.horizon_days, u.delisted_date, ends)
            if fwd is None:
                no_forward += 1
                continue
            counted = count_types(screens, th.use_type_d)
            prim = fwd[th.horizon_days]
            extra = {f"excess_final_{h}": (fwd[h]["excess_final"] if fwd[h] else float("nan")) for h in th.horizons}
            fa = screens["A"].facts
            extra.update({"netnet": fa.get("netnet"), "pbr": fa.get("pbr"), "equity_ratio": fa.get("equity_ratio")})
            f = snap.fin
            extra.update(_price_feats(snap.prices, th))
            extra.update({"oi_ttm": f.operating_income_ttm, "ni_ttm": f.net_income_ttm,
                          "oi_yoy": metrics.yoy(f.operating_income_ttm, snap.fin_year_ago.operating_income_ttm if snap.fin_year_ago else None),
                          "dilution": metrics.dilution(f, snap.fin_year_ago),
                          "cash_over_debt": ((metrics.liquid_cash_like(f) or 0.0) / f.interest_debt)
                          if f.interest_debt and metrics.liquid_cash_like(f) is not None else None})
            rows.append({**extra, **{"asof": asof, "code": u.code, "sector33": u.sector33, "mcap": snap.mcap,
                         "hit_A": screens["A"].hit, "hit_B": screens["B"].hit, "hit_C": screens["C"].hit,
                         "hit_D": screens["D"].hit, "n_types": len(counted), "types": "".join(counted),
                         "moved": prim["excess_max"] >= th.moved_excess, "excess_max": prim["excess_max"],
                         "excess_final": prim["excess_final"], "truncated": prim["truncated"]}})
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
    return BacktestResult(obs, summarize(obs, th), warnings, horizon_table(obs, th))


def _price_feats(sliced: pd.DataFrame, th: ValueThresholds) -> dict:
    """株価だけで作れる特徴(型の代用や、選定ルールの検証に使う)。値のまま残し、閾値は後から変えられる。"""
    n = len(sliced)
    closes = sliced["close"].to_numpy(dtype=float)
    vol60 = float(np.std(np.diff(closes[-61:]) / closes[-61:-1])) if n >= 62 else float("nan")
    return {"bars": n, "drawdown_5y": metrics.drawdown_from_peak(sliced, th.cyc_peak_window_days),
            "ret_20": metrics.ret_over(sliced, 20), "ret_60": metrics.ret_over(sliced, 60),
            "vol_surge": metrics.volume_surge(sliced, th.surge_mult, th.surge_within_days, th.surge_avg_window),
            "vol_60": vol60, "deviation": metrics.ma_deviation(sliced, th.ma_window)}


def run_price_backtest(universe: Universe, prices: PriceSource, topix: pd.DataFrame, dates: list[date],
                       th: ValueThresholds, size_ok=None, require_delisted: bool = False) -> BacktestResult:
    """**株価だけ**の過去検証(財務データが無くてもできる)。型D(25日線からの下方乖離)と、その元データを残す。

    足切りは、株価100円以上と、売買代金(20日平均)の下限だけ。時価総額は使えないので、規模は size_ok(コード→bool)で絞る。
    型A・B・C は財務が要るので、ここでは該当しない扱い。乖離率などの元の値を obs に残すので、閾値を変えた感度の確認もできる。
    銘柄ごとに株価を1度だけ読んで全基準日を処理する(メモリと時間の節約)。
    """
    warnings: list[str] = []
    if not universe.has_delisted():
        msg = ("銘柄マスタに上場廃止の銘柄がありません。生存者バイアスで成績が水増しされている恐れがあります。"
               "売られた銘柄ほど上場廃止になりやすいので、型Dは特に楽観側に偏ります")
        if require_delisted:
            raise ValueError(msg)
        warnings.append(msg)
    topix_ser = pd.Series(topix["close"].astype(float).values, index=topix["date"].values).sort_index()
    valid_dates = [d for d in dates if d in topix_ser.index]
    if len(valid_dates) < len(dates):
        warnings.append(f"TOPIX の株価が無く、検証から外した基準日: {len(dates) - len(valid_dates)}日")
    ends = {d: horizon_ends(d, th.horizons) for d in valid_dates}
    lo, hi = min(dates) - timedelta(days=120), max(dates) + timedelta(days=420)
    rows, no_forward = [], 0
    for u in universe.rows:
        if size_ok is not None and not size_ok(u.code):
            continue
        df = prices.daily(u.code, lo, hi)
        if df.empty:
            continue
        d_arr = df["date"].to_numpy()
        for asof in valid_dates:
            if u.listed_date is not None and u.listed_date > asof:
                continue
            if u.delisted_date is not None and u.delisted_date <= asof:
                continue
            n = int(np.searchsorted(d_arr, asof, side="right"))
            if n < max(th.ma_window, th.turnover_window) or d_arr[n - 1] != asof:
                continue
            sliced = df.iloc[:n]
            price = float(sliced.iloc[-1]["close"])
            turnover = metrics.avg_turnover(sliced, th.turnover_window)
            if price < th.min_price_yen or turnover is None or turnover < th.min_turnover_yen:
                continue
            dev = metrics.ma_deviation(sliced, th.ma_window)
            if dev is None:
                continue
            feats = _price_feats(sliced, th)
            fwd = _forward(df, topix_ser, asof, price, th.horizons, th.horizon_days, u.delisted_date, ends[asof])
            if fwd is None:
                no_forward += 1
                continue
            prim = fwd[th.horizon_days]
            row = {f"excess_final_{h}": (fwd[h]["excess_final"] if fwd[h] else float("nan")) for h in th.horizons}
            row.update(feats)
            row.update({"asof": asof, "code": u.code, "sector33": u.sector33, "turnover": turnover, "deviation": dev,
                        "hit_A": False, "hit_B": False, "hit_C": False, "hit_D": dev <= th.sector_deviation(u.sector33),
                        "n_types": 0, "types": "", "moved": prim["excess_max"] >= th.moved_excess,
                        "excess_max": prim["excess_max"], "excess_final": prim["excess_final"],
                        "truncated": prim["truncated"]})
            rows.append(row)
    obs = pd.DataFrame(rows)
    if no_forward:
        warnings.append(f"基準日の翌日以降の株価が無く外した観測: {no_forward}件")
    if obs.empty:
        warnings.append("観測が1件もありません")
        return BacktestResult(obs, pd.DataFrame(), warnings)
    return BacktestResult(obs, summarize(obs, th), warnings, horizon_table(obs, th))


def horizon_table(obs: pd.DataFrame, th: ValueThresholds) -> pd.DataFrame:
    """型ごと・期間ごとに、日付ごとの比較(date_paired_lift)で、ベースラインとの超過リターンの差を見る。

    「動いた割合」は主な期間(20営業日)だけの見方。A・B のように数か月かけて再評価される型は、
    長い期間(60・120・250営業日)の超過リターンで測らないと、効果があっても「差なし」になる。
    """
    rows = []
    for g in GROUPS:
        mask = _mask(obs, g)
        for h in th.horizons:
            col = f"excess_final_{h}"
            valid = obs[col].notna()
            sub = obs[mask & valid]
            r = date_paired_lift(obs, mask, col)
            if sub.empty:
                verdict = "該当なし"
            elif r["n_dates"] < th.min_dates:
                verdict = f"基準日数不足({r['n_dates']}日<{th.min_dates}日。結論を出さない)"
            elif r["t"] > 2.0 and r["lift"] > 0:
                verdict = "ベースラインより高い(要・別期間での再確認)"
            else:
                verdict = "ベースラインと差があるとは言えない"
            rows.append({"group": g, "horizon": h, "n": len(sub), "n_dates": r["n_dates"],
                         "mean_excess": float(sub[col].mean()) if len(sub) else float("nan"),
                         "baseline_mean": float(obs.loc[valid, col].mean()) if valid.any() else float("nan"),
                         "lift": r["lift"], "t": r["t"], "pos_share": r["pos_share"], "verdict": verdict})
    return pd.DataFrame(rows)


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


def perm_p_value(obs: pd.DataFrame, mask: pd.Series, draws: int, seed: int, stratified: bool = True) -> float:
    """「ベースラインから同じ数を無作為に選んだとき、これ以上の数が動く確率」(片側)。

    stratified=True(既定)は、**日付ごと**に、その日のベースラインから、その日の該当数だけ抽出する。
    同じ日の銘柄は値動きが連動する(相場の地合い)ため、日付をまたいで無作為に混ぜる検定は、
    地合いの良い時期に該当が偏っただけで「有意」と出る(偽陽性が増える)。層別にすると、地合いの影響が消える。
    抽出は超幾何分布で厳密に行う(速い)。
    """
    sub = obs[mask]
    if sub.empty:
        return float("nan")
    rng = np.random.default_rng(seed)
    observed = int(sub["moved"].sum())
    if stratified:
        total = np.zeros(draws, dtype=np.int64)
        counts = sub.groupby("asof").size()
        for d, nd in counts.items():
            base = obs.loc[obs["asof"] == d, "moved"]
            m, k = len(base), int(base.sum())
            total += rng.hypergeometric(k, m - k, nd, size=draws) if nd < m else k
    else:
        m, k, nd = len(obs), int(obs["moved"].sum()), len(sub)
        if nd >= m:
            return float("nan")
        total = rng.hypergeometric(k, m - k, nd, size=draws)
    return float((int((total >= observed).sum()) + 1) / (draws + 1))


def date_paired_lift(obs: pd.DataFrame, mask: pd.Series, col: str) -> dict[str, float]:
    """日付ごとに「該当群の平均 − その日のベースライン平均」を取り、日付をまたいで平均する(Fama-MacBeth 流)。

    日付を1つの観測として扱うので、同日の連動(地合い)に左右されない。t値と、プラスだった日の割合を返す。
    """
    sub = obs[mask & obs[col].notna()]
    base = obs[obs[col].notna()].groupby("asof")[col].mean()
    sel = sub.groupby("asof")[col].mean()
    diff = (sel - base.reindex(sel.index)).dropna()
    n = len(diff)
    if n < 2:
        return {"lift": float("nan"), "t": float("nan"), "n_dates": n, "pos_share": float("nan")}
    sd = float(diff.std(ddof=1))
    t = float(diff.mean() / (sd / math.sqrt(n))) if sd > 0 else (float("inf") if diff.mean() > 0 else 0.0)
    return {"lift": float(diff.mean()), "t": t, "n_dates": n, "pos_share": float((diff > 0).mean())}


def summarize(obs: pd.DataFrame, th: ValueThresholds) -> pd.DataFrame:
    """型ごとの「動いた」割合を、ベースライン(同じ足切りを通った全銘柄)と比べる。"""
    base_moved = obs["moved"]
    base_rate = float(base_moved.mean())
    lo0, hi0 = wilson(int(base_moved.sum()), len(obs))
    out = [{"group": "ベースライン(全体)", "n": len(obs), "n_dates": obs["asof"].nunique(),
            "moved_rate": base_rate, "ci_low": lo0, "ci_high": hi0, "mean_excess": obs["excess_final"].mean(),
            "median_excess": obs["excess_final"].median(), "baseline_rate": base_rate, "lift": 0.0,
            "p_value": float("nan"), "verdict": "—"}]
    for g in GROUPS:
        mask = _mask(obs, g)
        sub = obs[mask]
        n = len(sub)
        if n == 0:
            out.append({"group": g, "n": 0, "n_dates": 0, "moved_rate": float("nan"), "ci_low": float("nan"),
                        "ci_high": float("nan"), "mean_excess": float("nan"), "median_excess": float("nan"),
                        "baseline_rate": base_rate, "lift": float("nan"), "p_value": float("nan"), "verdict": "該当なし"})
            continue
        k = int(sub["moved"].sum())
        rate = k / n
        lo, hi = wilson(k, n)
        p = perm_p_value(obs, mask, th.permutation_draws, th.seed)
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
        if not res.horizons.empty:
            lines += ["", "## 期間別(日付ごとの比較。型の性格に合った期間で見る)", "",
                      "- 各基準日について「該当群の平均超過リターン − その日のベースラインの平均」を出し、日付をまたいで平均する(t値は日付を1観測としたもの)。",
                      "- A・B は数か月〜1年で再評価される想定なので、120・250営業日の行を主に見る。C・D は20・60営業日。",
                      "- **この表は群7×期間4=28のセルがある。偶然でも t>2 のセルが1〜2個は出る。判定に使うのは、事前に決めた主セル(docs/VALUE_VALIDATION.md §5 の H1〜H5)だけ。それ以外は探索であり、採用の根拠にしない。**", "",
                      "| 群 | 期間(営業日) | 観測数 | 基準日数 | 該当群の平均超過 | ベースライン平均 | 差(日付ごと) | t値 | 差がプラスだった日の割合 | 判定 |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
            for r in res.horizons.itertuples():
                f = lambda x: "—" if x != x else f"{x:.1%}"
                tt = "—" if r.t != r.t else f"{r.t:.2f}"
                lines.append(f"| {r.group} | {r.horizon} | {r.n} | {r.n_dates} | {f(r.mean_excess)} | {f(r.baseline_mean)} | "
                             f"{f(r.lift)} | {tt} | {f(r.pos_share)} | {r.verdict} |")
        lines += ["", "## 読み方", "",
                  "- p値は「同じ日のベースラインから、その日の該当数だけランダムに選んだとき、これ以上の数が動く確率」(片側。日付ごとの層別)。",
                  "- 同じ日の銘柄は値動きが連動するので、観測は独立ではない。**基準日数が少ないうちは p値を信用しない。**",
                  "- 検出力の目安(`scripts/value_power_analysis.py`、仮定つき):基準日24日(約2年の月次)では、20営業日で+2%程度以上の優位性しか確実には検出できない。それより小さい優位性は、「差なし」と出ても「無い」とは言えない。",
                  "- 「ベースラインより高い」と出ても、別の期間・別の相場で再確認するまで採用しない。",
                  "- 型が重なったほうが成績が良いか(「型が2つ以上」と「型が1つだけ」の比較)が、合議スコアの存在理由。"
                  "差が出なければ、この設計は見直す。"]
    return "\n".join(lines) + "\n"
