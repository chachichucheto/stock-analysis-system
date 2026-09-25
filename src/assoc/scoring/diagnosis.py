"""反応の診断のラベル。docs/DESIGN.md §6.6.1、docs/CONCEPT.md §8.2。

上から順に当てはめ、最初に当たったものをラベルとする(5 は 1 より優先して判定する)。
"""
from __future__ import annotations

from dataclasses import dataclass

from assoc.config import Thresholds

早耳の先回り = "早耳の先回り"
織り込み進行 = "織り込み進行"
時間差あり = "時間差あり"
無視されている疑い = "⚠ 無視されている疑い"
未発見 = "未発見"


@dataclass(frozen=True)
class DiagnosisInput:
    attention_high: bool
    """イベントの注目度が高いか(docs/DESIGN.md §7 の閾値で判定済みのもの)。"""
    tier1_reacted: bool
    """1段目の銘柄(または銘柄群・業種指数)が反応したか。"""
    candidate_reacted: bool
    """候補(2段目)の銘柄自身が反応したか。"""
    priced_in_value: float
    """織り込み度。"""


def diagnose(inp: DiagnosisInput, thresholds: Thresholds) -> str:
    """docs/DESIGN.md §6.6.1 の表のとおりの優先順位でラベルを1つ返す。"""
    # 5: 注目度が低いのに候補が反応した(1 より優先)
    if not inp.attention_high and inp.candidate_reacted:
        return 早耳の先回り
    # 1: 候補が反応した(織り込み度が完了未満)
    if inp.candidate_reacted and inp.priced_in_value < thresholds.priced_in_done:
        return 織り込み進行
    # 表にない組み合わせ(候補は反応したが織り込み度が完了以上)は、
    # 織り込みが進んだ状態そのものなので「織り込み進行」を返す(解釈。最終報告に記載)。
    if inp.candidate_reacted:
        return 織り込み進行
    # 2: 1段目が反応した、候補は反応していない
    if inp.tier1_reacted and not inp.candidate_reacted:
        return 時間差あり
    # 3・4: 1段目・候補とも反応していない
    if inp.attention_high:
        return 無視されている疑い
    return 未発見
