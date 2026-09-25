"""シナリオの終了判定。docs/CONCEPT.md §8.3。

シナリオ自身の時計(起動日からの経過営業日と想定期間)で判定する。上から順に当てはめ、
最初に当たったものを終了の理由とする。当たらなければ None(継続)を返す。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from assoc.config import Thresholds
from assoc.timeutil import business_days_between

材料が弱かった = "材料が弱かった"
連想が届かなかった = "連想が市場に届かなかった"
織り込みが完了した = "織り込みが完了した"
シナリオが崩れた = "シナリオが崩れた"
起動しなかった = "起動しなかった"


@dataclass(frozen=True)
class EndRuleInput:
    started: bool
    start_date: date | None
    """起動日(started=True のときのみ使う)。"""
    created_date: date
    """シナリオを立てた日(待機中の経過日数の起点)。"""
    eval_date: date
    expected_days: int
    tier1_reacted: bool
    candidate_reacted: bool
    priced_in_value: float
    end_condition_triggered: bool
    """終了条件(シナリオごとに個別に定めたもの)に当たったか。LLM・人の判定を引数で受け取る。"""


def check_end(inp: EndRuleInput, thresholds: Thresholds) -> str | None:
    if inp.end_condition_triggered:
        return シナリオが崩れた
    if not inp.started:
        elapsed = business_days_between(inp.created_date, inp.eval_date)
        if elapsed >= thresholds.not_started_days:
            return 起動しなかった
        return None
    if inp.start_date is None:
        raise ValueError("started=True のときは start_date が必要です")
    if inp.priced_in_value >= thresholds.priced_in_done:
        return 織り込みが完了した
    elapsed = business_days_between(inp.start_date, inp.eval_date)
    if elapsed > inp.expected_days:
        if not inp.tier1_reacted and not inp.candidate_reacted:
            return 材料が弱かった
        if inp.tier1_reacted and not inp.candidate_reacted:
            return 連想が届かなかった
    return None
