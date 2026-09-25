"""等級の確定(翌日)。docs/CONCEPT.md §5.3、docs/DESIGN.md §6.8。

check_basis・check_codes で指定された銘柄(または銘柄群・業種指数)の当日の
day_excess_sigma を計算し、reacted_sigma 以上なら「反応あり」とする。
- 暫定 S/A で反応なし → 1段階格下げ(S→A、A→B)
- 暫定 B/C で反応あり → 格付けは変えず「見落としの可能性」フラグを立てる
- check_basis == 'なし' → 検算せず、暫定をそのまま確定し「検算なし」とする
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from assoc.config import Thresholds
from assoc.market.indicators import day_excess_sigma

_GRADE_ORDER = ["S", "A", "B", "C"]

検算なし = "検算なし"
確定 = "確定"
格下げ = "反応なしのため1段階格下げ"
見落としの可能性 = "見落としの可能性"


@dataclass(frozen=True)
class GradeCheckResult:
    final_grade: str
    reacted: bool | None
    """None は検算していないことを示す(check_basis == 'なし' の場合)。"""
    max_sigma: float | None
    note: str


def check_grade(
    provisional_grade: str,
    check_basis: str,
    check_codes: list[str],
    date_: date,
    prices_by_code: dict[str, pd.DataFrame],
    topix: pd.DataFrame,
    window: int,
    thresholds: Thresholds,
) -> GradeCheckResult:
    if check_basis == "なし" or not check_codes:
        return GradeCheckResult(final_grade=provisional_grade, reacted=None, max_sigma=None, note=検算なし)

    sigmas = [
        day_excess_sigma(prices_by_code[c], topix, date_, window)
        for c in check_codes
        if c in prices_by_code
    ]
    if not sigmas:
        return GradeCheckResult(final_grade=provisional_grade, reacted=None, max_sigma=None, note=検算なし)

    max_sigma = max(sigmas)
    reacted = max_sigma >= thresholds.reacted_sigma

    final_grade = provisional_grade
    note = 確定
    if provisional_grade in ("S", "A") and not reacted:
        idx = _GRADE_ORDER.index(provisional_grade)
        final_grade = _GRADE_ORDER[idx + 1]
        note = 格下げ
    elif provisional_grade in ("B", "C") and reacted:
        note = 見落としの可能性

    return GradeCheckResult(final_grade=final_grade, reacted=reacted, max_sigma=max_sigma, note=note)
