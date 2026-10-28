"""連想の答え合わせ(D50-C):機械が出した連想先に、その後本当に動いた銘柄がどれだけ入っていたか。

ニュースが先で株価があと。株価は『連想先が当たったか』を測るためだけに使い、検知・連想には使わない。
  - 問題 :ある日の銘柄(材料が重なった銘柄など)と、機械が出した連想先(related)
  - 答え :その日の終値から h 営業日後までの騰落率が、同じ日の銘柄の中央値より thresh 以上高い銘柄
  - 指標 :連想先のうち答えだった割合(precision)を、答えの基準率(全銘柄で答えだった割合)で割った lift。
           lift が 1 を超えなければ、連想先は無作為に選んだのと変わらない。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

HORIZON = 5
THRESH = 0.05


def load_closes(history_path, gap_path) -> pd.DataFrame:
    """date × code の終値。履歴と補完(gap)を重ね、重なる日は補完を優先する。"""
    import duckdb
    con = duckdb.connect()
    try:
        frames = []
        for p in (history_path, gap_path):
            if p:
                frames.append(con.execute(
                    "SELECT CAST(date AS DATE) AS date, replace(code, '.T', '') AS code, close FROM read_parquet(?)",
                    [str(p)]).df().assign(pri=0 if p == history_path else 1))
    finally:
        con.close()
    df = pd.concat(frames, ignore_index=True).sort_values(["code", "date", "pri"]).drop_duplicates(["code", "date"], keep="last")
    df["date"] = pd.to_datetime(df["date"]).dt.date           # index は datetime.date にそろえる(forward_excess が date で比べる)
    return df.pivot(index="date", columns="code", values="close").sort_index()


def forward_excess(closes: pd.DataFrame, day, horizon: int = HORIZON) -> pd.Series | None:
    """day の終値から horizon 営業日後までの騰落率 − 同じ期間の中央値。株価が足りなければ None。"""
    idx = [d for d in closes.index if d <= day]
    if not idx:
        return None
    pos = closes.index.get_loc(idx[-1])
    if idx[-1] != day or pos + horizon >= len(closes.index):
        return None
    ret = closes.iloc[pos + horizon] / closes.iloc[pos] - 1
    ret = ret.dropna()
    return ret - ret.median()


@dataclass
class CaseScore:
    day: str
    code: str
    related: int            # 株価のある連想先の数
    hits: int
    base_rate: float
    own_excess: float | None

    @property
    def precision(self) -> float | None:
        return self.hits / self.related if self.related else None

    @property
    def lift(self) -> float | None:
        return None if not self.related or not self.base_rate else self.precision / self.base_rate


def score_case(day: str, code: str, related_codes: list[str], excess: pd.Series, thresh: float = THRESH) -> CaseScore:
    have = [c for c in related_codes if c in excess.index]
    return CaseScore(day=day, code=code, related=len(have), hits=sum(1 for c in have if excess[c] >= thresh),
                     base_rate=float((excess >= thresh).mean()),
                     own_excess=float(excess[code]) if code in excess.index else None)


def summarize(scores: list[CaseScore]) -> dict:
    used = [s for s in scores if s.related]
    if not used:
        return {"cases": len(scores), "with_related": 0}
    rel = sum(s.related for s in used)
    hit = sum(s.hits for s in used)
    base = sum(s.base_rate * s.related for s in used) / rel          # 連想先の数で重みを付けた基準率
    return {"cases": len(scores), "with_related": len(used), "related_total": rel, "hits": hit,
            "precision": hit / rel, "base_rate": base, "lift": (hit / rel) / base if base else None}
