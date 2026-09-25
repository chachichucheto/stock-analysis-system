"""進行中のシナリオの候補を評価し、自信度ランキングを作る(docs/DESIGN.md §6.6)。

計算の部品(scoring/、tracking/、market/indicators.py)をつなぐだけで、判定の規則はここに書かない。
ただし「実現確度の補正」だけはここで行う(CONCEPT §8.4)。補正の係数は暫定で、
振り返りで見直す(変えたら DECISIONS.md に記録する)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from assoc.config import Thresholds
from assoc.market import indicators as ind
from assoc.market.prices import COLUMNS, PriceSource
from assoc.scoring.confidence import confidence as calc_confidence
from assoc.scoring.diagnosis import DiagnosisInput, diagnose
from assoc.scoring.freshness import FreshnessInput, freshness as calc_freshness
from assoc.scoring.picks import CandidateInput, RankedPick, rank_picks
from assoc.scoring.priced_in import priced_in as calc_priced_in
from assoc.state import ScenarioState
from assoc.timeutil import business_days_between
from assoc.tracking.trade_aids import TradeAidsResult, trade_aids

# 実現確度の補正(暫定。CONCEPT §8.4)
IGNORED_FACTOR = 0.5            # 「無視されている疑い」
DENIED_ARROW_FACTOR = 0.7       # 否定された矢印1本ごと
UNCLEAR_DIRECTION_FACTOR = 0.8  # プラスにもマイナスにも読める
STRENGTH_STEP = 0.10            # シナリオの強さが1段階変わるごと
TEMPERATURE_FACTOR = {"上昇中": 1.1, "横ばい": 1.0, "冷却中": 0.9}
MAX_PROB = 0.95
STRENGTHS = ["Weak", "Weak+", "Medium", "Strong"]
LOOKBACK_DAYS = 120


class PriceLoader:
    """銘柄ごとの株価を一度だけ読み込む。外国株(1段目の検算用)は別の PriceSource を使う。"""

    def __init__(self, source: PriceSource, eval_date: date, foreign: PriceSource | None = None):
        self.source, self.foreign, self.eval_date = source, foreign, eval_date
        self._cache: dict[str, pd.DataFrame] = {}

    def get(self, code: str) -> pd.DataFrame:
        if code not in self._cache:
            start = self.eval_date - timedelta(days=LOOKBACK_DAYS * 2)
            src = self.source if _is_jp_code(code) or self.foreign is None else self.foreign
            try:
                self._cache[code] = src.daily(code, start, self.eval_date)
            except Exception:           # 取得できない銘柄は「データ無し」として扱い、処理は止めない
                self._cache[code] = pd.DataFrame(columns=COLUMNS)
        return self._cache[code]

    def loaded(self) -> dict[str, pd.DataFrame]:
        return {k: v for k, v in self._cache.items() if not v.empty}


def _is_jp_code(code: str) -> bool:
    return len(code) == 4 and code[0].isdigit()


@dataclass
class CandidateEval:
    scenario: ScenarioState
    candidate: dict[str, Any]
    freshness: float
    priced_in: float
    excess: float
    remaining_room: float
    realization_prob: float
    prob_factors: list[str]
    confidence: float
    stars: int
    diagnosis: str
    tier1_reacted: bool
    candidate_reacted: bool
    remaining_days: int | None
    aids: TradeAidsResult | None
    price: float | None
    has_prices: bool
    rank: int = 0
    tier: str = "監視"
    excluded_reason: str | None = None
    notes: list[str] = field(default_factory=list)


def _reacted_since(prices: pd.DataFrame, topix: pd.DataFrame, since: date, until: date, th: Thresholds) -> bool:
    """起点の日以降のどこかの日で、当日の TOPIX 超過が普段の値動きの reacted_sigma 倍以上だったか。"""
    if prices.empty or topix.empty:
        return False
    for d in prices["date"]:
        if since <= d <= until:
            try:
                if ind.day_excess_sigma(prices, topix, d, th.vol_window) >= th.reacted_sigma:
                    return True
            except (ValueError, ZeroDivisionError, KeyError, IndexError):
                continue
    return False


def adjust_probability(base: float, sc: ScenarioState, cand: dict[str, Any], diagnosis: str,
                       temperature: str | None, initial_strength: str) -> tuple[float, list[str]]:
    prob, notes = base, [f"LLM の推定 {base:.0%}"]
    ratio = sc.verified_ratio
    factor = 0.8 + 0.4 * ratio
    prob *= factor
    notes.append(f"裏取り {ratio:.0%} 確認 ×{factor:.2f}")
    if sc.denied_arrows:
        f = DENIED_ARROW_FACTOR ** sc.denied_arrows
        prob *= f
        notes.append(f"否定された矢印 {sc.denied_arrows} 本 ×{f:.2f}")
    if not cand.get("direction_clear", True):
        prob *= UNCLEAR_DIRECTION_FACTOR
        notes.append(f"方向が曖昧 ×{UNCLEAR_DIRECTION_FACTOR}")
    if diagnosis == "⚠ 無視されている疑い":
        prob *= IGNORED_FACTOR
        notes.append(f"無視されている疑い ×{IGNORED_FACTOR}")
    if temperature in TEMPERATURE_FACTOR and TEMPERATURE_FACTOR[temperature] != 1.0:
        prob *= TEMPERATURE_FACTOR[temperature]
        notes.append(f"テーマの温度 {temperature} ×{TEMPERATURE_FACTOR[temperature]}")
    if sc.strength in STRENGTHS and initial_strength in STRENGTHS:
        steps = STRENGTHS.index(sc.strength) - STRENGTHS.index(initial_strength)
        if steps:
            f = 1 + STRENGTH_STEP * steps
            prob *= f
            notes.append(f"強さ {initial_strength}→{sc.strength} ×{f:.2f}")
    return min(max(prob, 0.0), MAX_PROB), notes


def evaluate(scenarios: dict[str, ScenarioState], loader: PriceLoader, topix_code: str, eval_date: date,
             th: Thresholds, *, attention_high: dict[str, bool] | None = None,
             temperatures: dict[str, str] | None = None, initial_strength: dict[str, str] | None = None,
             low_tier_only: set[str] | None = None, kanri: set[str] | None = None,
             calendar: dict[str, dict[str, Any]] | None = None) -> list[CandidateEval]:
    """進行中・待機中のシナリオの全候補を評価し、順位を付けて返す。"""
    attention_high = attention_high or {}
    temperatures = temperatures or {}
    initial_strength = initial_strength or {}
    low_tier_only = low_tier_only or set()
    kanri = kanri or set()
    calendar = calendar or {}
    topix = loader.get(topix_code)
    evals: list[CandidateEval] = []

    for sc in scenarios.values():
        if not sc.is_active:
            continue
        started = sc.status == "進行中" and sc.started_date is not None
        clock = sc.clock_date or sc.created_date
        fresh = calc_freshness(FreshnessInput(started=started, start_date=clock,
                                              expected_days=sc.expected_days, eval_date=eval_date))
        remaining_days = None
        if started:
            remaining_days = sc.expected_days - business_days_between(clock, eval_date)
        temp = next((temperatures[t] for t in sc.theme_ids if t in temperatures), None)
        tier1_codes = [c["code"] for c in sc.candidates if c.get("stage") == 1]
        tier1_reacted = started and any(
            _reacted_since(loader.get(code), topix, sc.started_date, eval_date, th) for code in tier1_codes)

        for cand in sc.candidates:
            prices = loader.get(cand["code"])
            has_prices = not prices.empty and not topix.empty
            notes: list[str] = []
            pin, excess = 0.0, 0.0
            median = sc.expected_rise.get("median") or 0.0
            if started and has_prices and median > 0:
                try:
                    r = calc_priced_in(prices, topix, sc.started_date, eval_date, median,
                                       sc.expected_rise.get("version", 1))
                    pin, excess = r.value, r.excess
                except (ValueError, KeyError, IndexError) as e:
                    notes.append(f"織り込み度を計算できない: {e}")
            elif not has_prices:
                notes.append("株価データが無い")
            cand_reacted = started and has_prices and _reacted_since(prices, topix, sc.started_date, eval_date, th)
            label = diagnose(DiagnosisInput(attention_high=attention_high.get(sc.event_id, False),
                                            tier1_reacted=tier1_reacted, candidate_reacted=cand_reacted,
                                            priced_in_value=pin), th)
            prob, factors = adjust_probability(cand["realization_prob"], sc, cand, label, temp,
                                               initial_strength.get(sc.scenario_id, sc.strength))
            conf = calc_confidence(prob, median, pin, fresh, th)
            aids, price = None, None
            if has_prices:
                price = float(prices["close"].iloc[-1])
                cal = calendar.get(cand["code"], {})
                try:
                    aids = trade_aids(eval_date, sc.expected_days, price, prices, cal.get("earnings_date"),
                                      cal.get("ex_rights_date"), bool(cal.get("credit_restriction")), th)
                except (ValueError, KeyError, IndexError) as e:
                    notes.append(f"売買の目安を計算できない: {e}")
            evals.append(CandidateEval(
                scenario=sc, candidate=cand, freshness=fresh, priced_in=pin, excess=excess,
                remaining_room=conf.remaining_room, realization_prob=prob, prob_factors=factors,
                confidence=conf.confidence, stars=conf.stars, diagnosis=label, tier1_reacted=tier1_reacted,
                candidate_reacted=cand_reacted, remaining_days=remaining_days, aids=aids, price=price,
                has_prices=has_prices, notes=notes))

    _rank(evals, loader, eval_date, th, low_tier_only, kanri)
    return evals


def _rank(evals: list[CandidateEval], loader: PriceLoader, eval_date: date, th: Thresholds,
          low_tier_only: set[str], kanri: set[str]) -> None:
    inputs: list[CandidateInput] = []
    by_key: dict[tuple[str, str], CandidateEval] = {}
    for e in evals:
        prices = loader.get(e.candidate["code"])
        turnover = ret20 = 0.0
        if e.has_prices:
            try:
                turnover = ind.avg_turnover(prices, eval_date, th.vol_window)
                ret20 = ind.return_over(prices, eval_date, th.surged_days)
            except (ValueError, KeyError, IndexError):
                pass
        side = e.candidate.get("side", "long")
        inputs.append(CandidateInput(
            scenario_id=e.scenario.scenario_id, code=e.candidate["code"],
            company_name=e.candidate.get("master_name") or e.candidate["company_name"],
            confidence=e.confidence, side=side, avg_turnover_yen=turnover, price=e.price or 0.0,
            return_20d=ret20, evidence_all_low_tier=e.scenario.scenario_id in low_tier_only,
            is_kanri_seiri=e.candidate["code"] in kanri))
        by_key[(e.scenario.scenario_id, e.candidate["code"])] = e
    ranked: list[RankedPick] = rank_picks(inputs, th)
    for r in ranked:
        e = by_key[(r.candidate.scenario_id, r.candidate.code)]
        e.rank, e.tier, e.excluded_reason = r.rank, r.tier, r.excluded_reason
        if not e.has_prices:
            e.excluded_reason = "株価データが無い"
    # 待機中(未起動)のシナリオの候補は本命にしない。時計が止まっているので「動いた」の判定も始めない
    for e in evals:
        if e.scenario.status == "待機":
            e.excluded_reason = "待機中(未起動)"
    evals.sort(key=lambda e: e.rank)
    promoted = 0
    for e in evals:
        if e.excluded_reason is None and e.candidate.get("side", "long") == "long" and promoted < th.max_picks:
            e.tier = "本命"
            promoted += 1
        else:
            e.tier = "監視"
