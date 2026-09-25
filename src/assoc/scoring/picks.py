"""候補の一覧から本命を選ぶ。docs/CONCEPT.md §8.4、docs/DESIGN.md §8.1。

自信度の高い順にランキングを作り、除外条件に当たるものは除外理由を付けたうえで
ランキングには残すが本命にはしない。side='caution' の候補も本命にしない。
"""
from __future__ import annotations

from dataclasses import dataclass

from assoc.config import Thresholds

本命 = "本命"
監視 = "監視"


@dataclass(frozen=True)
class CandidateInput:
    scenario_id: str
    code: str
    company_name: str
    confidence: float
    side: str
    """'long' | 'caution'。"""
    avg_turnover_yen: float
    """20日平均の売買代金(円)。"""
    price: float
    return_20d: float
    """直近20営業日の騰落率。"""
    evidence_all_low_tier: bool
    """裏取りの証拠のすべての矢印が信頼度3・4の情報源だけに基づくか。"""
    is_kanri_seiri: bool = False
    """監理・整理ポストの銘柄か。"""


@dataclass(frozen=True)
class RankedPick:
    rank: int
    candidate: CandidateInput
    tier: str
    """'本命' | '監視'。"""
    excluded_reason: str | None
    """本命にしなかった理由(除外条件に当たらず、単に順位が max_picks 圏外の場合も None)。"""


def exclusion_reason(c: CandidateInput, thresholds: Thresholds) -> str | None:
    """docs/DESIGN.md §8.1 の除外条件。当たれば理由を返す(本命にはしないが、ランキングには残す)。"""
    if c.side == "caution":
        return "売り方向(caution)の候補"
    if c.is_kanri_seiri:
        return "監理・整理ポストの銘柄"
    if c.avg_turnover_yen < thresholds.min_turnover_yen:
        return "流動性の下限を満たさない"
    if c.price < thresholds.min_price_yen:
        return "株価100円未満"
    if c.return_20d > thresholds.surged_return:
        return "直近ですでに急騰している"
    if c.evidence_all_low_tier:
        return "裏取りの証拠が信頼度3・4の情報源だけ"
    return None


def rank_picks(candidates: list[CandidateInput], thresholds: Thresholds) -> list[RankedPick]:
    """自信度の降順でランキングを作り、上位 thresholds.max_picks 件(除外条件に当たらないもの)を本命にする。"""
    ordered = sorted(candidates, key=lambda c: c.confidence, reverse=True)
    results: list[RankedPick] = []
    picked = 0
    for i, c in enumerate(ordered, start=1):
        reason = exclusion_reason(c, thresholds)
        tier = 監視
        if reason is None and picked < thresholds.max_picks:
            tier = 本命
            picked += 1
        results.append(RankedPick(rank=i, candidate=c, tier=tier, excluded_reason=reason))
    return results
