"""財務データ・銘柄マスタの読み込みと、時点を守った(Point-in-Time)参照。

**ここが過去検証の信頼性の土台**。財務は `disclosed_date`(開示日)が asof より前のものだけを返す
(asof 当日の開示は、引け後に出るものがあるので使わない=保守的)。

ローカルで用意するファイルの形式は docs/VALUE_DESIGN.md §5 に書いてある。このリポジトリはデータを持たない。
"""
from __future__ import annotations

import csv
from dataclasses import fields
from datetime import date, timedelta
from pathlib import Path

from assoc.value.models import PERIODS, FinancialRow, UniverseRow

_FIN_NUMERIC = [f.name for f in fields(FinancialRow) if f.name not in ("code", "period_end", "disclosed_date", "period")]
FIN_REQUIRED = ["code", "period_end", "disclosed_date", "period"]
UNIVERSE_REQUIRED = ["code", "name", "market", "sector33", "listed_date"]


def _date(s: str, where: str) -> date:
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        raise ValueError(f"{where}: 日付 {s!r} は YYYY-MM-DD で書いてください") from None


def _opt_date(s: str | None, where: str) -> date | None:
    return _date(s, where) if s and s.strip() else None


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if s in ("", "NA", "N/A", "-", "nan"):
        return None
    return float(s)


def _flag(s: str | None) -> bool:
    return (s or "").strip().lower() in ("1", "true", "yes", "y", "○")


def _read_csv(path: Path, required: list[str]) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: 必要な列がありません: {missing}")
        return list(reader)


class FinancialsStore:
    def __init__(self, rows: list[FinancialRow]):
        self.rows = rows
        self._by_code: dict[str, list[FinancialRow]] = {}
        for r in rows:
            self._by_code.setdefault(r.code, []).append(r)

    @classmethod
    def from_csv(cls, path: Path) -> "FinancialsStore":
        rows = []
        for i, d in enumerate(_read_csv(path, FIN_REQUIRED), start=2):
            where = f"{path}:{i}行目"
            if d["period"] not in PERIODS:
                raise ValueError(f"{where}: period は {PERIODS} のどれか(実際: {d['period']!r})")
            values = {k: _num(d.get(k)) for k in _FIN_NUMERIC}
            rows.append(FinancialRow(code=d["code"].strip(), period_end=_date(d["period_end"], where),
                                     disclosed_date=_date(d["disclosed_date"], where), period=d["period"], **values))
        return cls(rows)

    def codes(self) -> set[str]:
        return set(self._by_code)

    def known_rows(self, code: str, asof: date) -> list[FinancialRow]:
        """asof より前に開示された決算を、期末ごとに最新の開示版だけ、期末の新しい順で返す。"""
        latest: dict[tuple[date, str], FinancialRow] = {}
        for r in self._by_code.get(code, []):
            if r.disclosed_date < asof:                 # 先読み防止:当日の開示は使わない
                key = (r.period_end, r.period)
                if key not in latest or r.disclosed_date > latest[key].disclosed_date:
                    latest[key] = r
        return sorted(latest.values(), key=lambda r: r.period_end, reverse=True)

    @staticmethod
    def year_ago(rows: list[FinancialRow], row: FinancialRow, tol_days: int = 25) -> FinancialRow | None:
        """row の約1年前の期末の行(四半期の前年同期)。"""
        target = row.period_end - timedelta(days=365)
        best = None
        for r in rows:
            gap = abs((r.period_end - target).days)
            if r.period_end < row.period_end and gap <= tol_days and (best is None or gap < best[0]):
                best = (gap, r)
        return best[1] if best else None


class Universe:
    def __init__(self, rows: list[UniverseRow]):
        self.rows = rows
        self._by_code = {r.code: r for r in rows}

    @classmethod
    def from_csv(cls, path: Path) -> "Universe":
        rows = []
        for i, d in enumerate(_read_csv(path, UNIVERSE_REQUIRED), start=2):
            where = f"{path}:{i}行目"
            rows.append(UniverseRow(code=d["code"].strip(), name=d["name"].strip(), market=d["market"].strip(),
                                    sector33=d["sector33"].strip(), listed_date=_opt_date(d["listed_date"], where),
                                    delisted_date=_opt_date(d.get("delisted_date"), where),
                                    monitoring=_flag(d.get("monitoring")), going_concern=_flag(d.get("going_concern"))))
        return cls(rows)

    def get(self, code: str) -> UniverseRow | None:
        return self._by_code.get(code)

    def members(self, d: date) -> list[UniverseRow]:
        """d の時点で上場していた銘柄。上場前・上場廃止後は含めない。"""
        out = []
        for r in self.rows:
            if r.listed_date is not None and r.listed_date > d:
                continue
            if r.delisted_date is not None and r.delisted_date <= d:
                continue
            out.append(r)
        return out

    def has_delisted(self) -> bool:
        """上場廃止の銘柄が1つでも入っているか。無ければ生存者バイアスの恐れ(過去検証の警告に使う)。"""
        return any(r.delisted_date is not None for r in self.rows)
