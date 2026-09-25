"""売りのサイン。docs/CONCEPT.md §9.1、docs/DESIGN.md §8.3。

過去に本命とした銘柄について、次の4つを検出する。
- 崩れる条件に当たるニュースが出た(引数で受け取る)
- シナリオが弱体化・終了した(引数で受け取る)
- 織り込み度が priced_in_near 以上(完了が近い)
- 想定期間の残りが ending_soon_days 以下
"""
from __future__ import annotations

from dataclasses import dataclass

from assoc.config import Thresholds

崩れる条件に当たるニュース = "崩れる条件に当たるニュースが出た"
シナリオの弱体化 = "シナリオが弱体化・終了した"
織り込み完了間近 = "織り込み度が70%以上(完了が近い)"
想定期間終盤 = "想定期間の終わりが近い"


@dataclass(frozen=True)
class SellSignalInput:
    break_condition_triggered: bool
    scenario_weakened_or_dead: bool
    priced_in_value: float
    remaining_days: int
    """想定期間の残り営業日数(expected_days − 経過営業日数)。"""


def sell_signals(inp: SellSignalInput, thresholds: Thresholds) -> list[str]:
    signals: list[str] = []
    if inp.break_condition_triggered:
        signals.append(崩れる条件に当たるニュース)
    if inp.scenario_weakened_or_dead:
        signals.append(シナリオの弱体化)
    if inp.priced_in_value >= thresholds.priced_in_near:
        signals.append(織り込み完了間近)
    if inp.remaining_days <= thresholds.ending_soon_days:
        signals.append(想定期間終盤)
    return signals
