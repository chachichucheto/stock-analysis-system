"""ローカルで用意したデータの点検(docs/VALUE_LOCAL_KICKOFF.md フェーズB)。

過去検証は、データの欠けと日付の誤りで簡単に水増しされる。検証の前に必ずここを通す。
errors は検証を始める前に直すもの、warnings は結果の読み方に関わるもの。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from assoc.market.prices import PriceSource
from assoc.value.data import FinancialsStore, Universe

CORE_FIELDS = ("cash", "receivables", "total_liabilities", "equity", "total_assets", "shares_ex_treasury")


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)


def check_data(universe: Universe, fin: FinancialsStore, prices: PriceSource | None = None,
               sample_date: date | None = None, sample_size: int = 100) -> CheckResult:
    r = CheckResult()
    codes = [u.code for u in universe.rows]
    dup = sorted({c for c in codes if codes.count(c) > 1})
    if dup:
        r.errors.append(f"銘柄マスタに重複したコードがあります: {dup[:10]}")
    for u in universe.rows:
        if u.listed_date is None:
            r.warnings.append(f"{u.code}: 上場日が空です(上場前の期間に混入する恐れ)")
        elif u.delisted_date is not None and u.delisted_date <= u.listed_date:
            r.errors.append(f"{u.code}: 上場廃止日が上場日以前です")
    r.info.append(f"銘柄マスタ {len(universe.rows)}銘柄(うち上場廃止 {sum(1 for u in universe.rows if u.delisted_date)})")
    if not universe.has_delisted():
        r.warnings.append("上場廃止の銘柄がありません。生存者バイアスで過去検証が水増しされます")

    unknown = sorted(fin.codes() - set(codes))
    if unknown:
        r.warnings.append(f"財務データにあるが銘柄マスタに無いコード {len(unknown)}件(例: {unknown[:5]})")
    no_fin = [u.code for u in universe.rows if u.code not in fin.codes()]
    if no_fin:
        r.warnings.append(f"財務データが1行も無い銘柄 {len(no_fin)}件(例: {no_fin[:5]})。この銘柄は候補になれません")
    r.info.append(f"財務データ {len(fin.rows)}行 / {len(fin.codes())}銘柄")

    bad_dates = [x for x in fin.rows if x.disclosed_date < x.period_end]
    if bad_dates:
        r.errors.append(f"開示日が期末より前の行が {len(bad_dates)}件あります(例: {bad_dates[0].code} "
                        f"{bad_dates[0].period_end}→{bad_dates[0].disclosed_date})。先読みの原因になります")
    late = [x for x in fin.rows if (x.disclosed_date - x.period_end).days > 150]
    if late:
        r.warnings.append(f"期末から150日超あとの開示が {len(late)}件あります(訂正・遅延か、日付の誤りか確認)")
    nonpos = [x for x in fin.rows if x.shares_ex_treasury is not None and x.shares_ex_treasury <= 0]
    if nonpos:
        r.errors.append(f"発行済株式数が0以下の行が {len(nonpos)}件あります")
    if fin.rows:
        for f in CORE_FIELDS:
            miss = sum(1 for x in fin.rows if getattr(x, f) is None) / len(fin.rows)
            if miss > 0.2:
                r.warnings.append(f"{f} の欠損が {miss:.0%} あります。この項目に依存する型が該当しにくくなります")
        fy = [x for x in fin.rows if x.period == "FY"]
        per_code = len(fy) / max(len({x.code for x in fy}), 1)
        r.info.append(f"本決算の行数: 1銘柄あたり平均 {per_code:.1f}期(型Bには5期以上が必要)")

    if prices is not None and sample_date is not None:
        members = universe.members(sample_date)[:sample_size]
        if members:
            have = sum(1 for u in members
                       if not prices.daily(u.code, sample_date - timedelta(days=10), sample_date).empty)
            r.info.append(f"{sample_date} の株価の有無(先頭{len(members)}銘柄): {have}/{len(members)}")
            if have / len(members) < 0.8:
                r.warnings.append(f"{sample_date} に株価がある銘柄が {have}/{len(members)} しかありません(株価DBの欠けを確認)")
    return r
