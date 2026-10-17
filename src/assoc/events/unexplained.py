"""原因が分からない急騰・急落銘柄の検出(docs/DECISIONS.md D44)。

背景:2026-10-15、住友ファーマが+15.8%(出来高は普段の4〜6倍)になったが、取り込んだニュースにも開示にも社名が出てこず、
入力パックのどこにも現れなかった。速報(alerts.py)は通知するだけで、判定には渡らない。
株価が大きく動き、出来高も増えているのに、見出し・開示に社名が無い銘柄を機械で拾い、夜の手順で原因をWebで調べさせる。

株価は連想側の空白補完ファイル(prices_gap.parquet)だけを1回で読む(銘柄ごとに全層を読むと数百銘柄で遅いため)。
空白補完は、prepare が代表銘柄に加えて既存の555銘柄も更新する(market/theme_returns.py)。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from assoc.timeutil import JST

_NAME_SUFFIXES = ("ホールディングス", "グループ", "ＨＤ", "HD", "株式会社")


def _aliases(name: str) -> list[str]:
    """見出しに出うる社名の形(正式名称と、末尾の『ホールディングス』などを外したもの)。3文字未満は一般語と紛れるので使わない。"""
    out = [name]
    for suf in _NAME_SUFFIXES:
        if name.endswith(suf):
            out.append(name[: -len(suf)])
    return [a for a in dict.fromkeys(out) if len(a) >= 3]


def load_gap_prices(gap_path: Path, since: date) -> pd.DataFrame:
    import duckdb
    if not gap_path.exists():
        return pd.DataFrame(columns=["code", "date", "close", "volume"])
    con = duckdb.connect()
    try:
        df = con.execute("SELECT code, CAST(date AS DATE) AS date, close, volume FROM read_parquet(?) WHERE date >= ? "
                         "ORDER BY code, date", [str(gap_path), since]).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def explained_codes(con, names: dict[str, str], codes: list[str], since: datetime) -> set[str]:
    """since 以降の見出し・開示に、社名または銘柄コードが出てくる銘柄。"""
    titles = [r[0] or "" for r in con.execute(
        "SELECT title FROM news_item WHERE first_observed_at >= ?", [since]).fetchall()]
    discl = con.execute("SELECT code, company_name, title FROM disclosure WHERE first_observed_at >= ?", [since]).fetchall()
    blob = "\n".join(titles)
    out: set[str] = set()
    disclosed = {str(c)[:4] for c, _n, _t in discl if c} | {str(n) for _c, n, _t in discl if n}
    for code in codes:
        name = names.get(code) or names.get(code + "0") or ""
        if (name and any(a in blob for a in _aliases(name))) or f"<{code}>" in blob or f"＜{code}＞" in blob \
                or f"【{code}】" in blob or code in disclosed or (name and name in disclosed):
            out.add(code)
    return out


def detect_unexplained_movers(con, gap_path: Path, day: date, th, names: dict[str, str], topix_code: str,
                              *, max_items: int = 10, lookback_sessions: int = 2) -> dict:
    prices = load_gap_prices(gap_path, day - timedelta(days=60))
    if prices.empty:
        return {"as_of": None, "items": [], "explained_count": 0, "checked": 0}
    topix = prices[prices["code"] == topix_code].set_index("date")["close"]
    topix_ret = (topix / topix.shift(1) - 1).dropna()
    last_day = prices["date"].max()
    cands: list[dict] = []
    checked = 0
    for code, g in prices.groupby("code"):
        if code == topix_code:
            continue
        g = g.dropna(subset=["close"]).sort_values("date")
        if len(g) < 10 or g["date"].iloc[-1] < last_day - timedelta(days=4):
            continue
        checked += 1
        ret = (g["close"] / g["close"].shift(1) - 1)
        g = g.assign(ret=ret)
        recent = g.tail(lookback_sessions)
        prior = g.iloc[:-lookback_sessions]
        base_ret = prior["ret"].dropna().tail(20)
        base_vol = prior["volume"].tail(20)
        base_turn = (prior["close"] * prior["volume"]).tail(20)
        if len(base_ret) < 8 or base_vol.mean() <= 0:
            continue
        vol_sd = float(base_ret.std(ddof=1)) or 1e-9
        if float(base_turn.mean()) < th.min_turnover_yen:
            continue
        best = None
        for _, r in recent.iterrows():
            if pd.isna(r["ret"]) or r["date"] not in topix_ret.index or r["date"] < last_day - timedelta(days=3):
                continue            # 直近の動きだけ見る(株価の更新が止まっている銘柄の古い動きは、対象にしない)
            excess = float(r["ret"] - topix_ret[r["date"]])
            sigma = excess / vol_sd
            vratio = float(r["volume"] / base_vol.mean())
            if (abs(excess) >= th.unexplained_min_excess and abs(sigma) >= th.unexplained_min_sigma
                    and vratio >= th.unexplained_min_volume_ratio):
                if best is None or abs(excess) > abs(best["excess_1d"]):
                    best = {"code": code, "date": r["date"].isoformat(), "ret_1d": round(float(r["ret"]), 4),
                            "excess_1d": round(excess, 4), "sigma": round(sigma, 1), "volume_ratio": round(vratio, 1),
                            "avg_turnover_yen": int(base_turn.mean())}
        if best:
            cands.append(best)
    first = min((date.fromisoformat(c["date"]) for c in cands), default=day)
    since = datetime.combine(first - timedelta(days=2), time.min, tzinfo=JST)   # 動いた日の少し前から、見出し・開示を探す
    explained = explained_codes(con, names, [c["code"] for c in cands], since)
    items = []
    for c in sorted(cands, key=lambda c: abs(c["excess_1d"]), reverse=True):
        if c["code"] in explained:
            continue
        c["name"] = names.get(c["code"]) or names.get(c["code"] + "0") or ""
        items.append(c)
    return {"as_of": last_day.isoformat(), "items": items[:max_items], "explained_count": len(cands) - len(items),
            "checked": checked, "total_unexplained": len(items)}
