"""イベントの足切り(docs/DESIGN.md §6.2)。

通す条件(いずれか1つ)
  (a) 注目度(媒体数・GDELT・Wikipedia を合成)が、過去90日のイベント分布の上位5%
  (b) 注目度の前日比の伸びが、過去90日の伸びの分布の上位5%
  (c) 株価への影響が大きい種類の開示(大量保有報告書・TOB・資本業務提携・業績予想の大幅修正 など)
ただし
  (d) novelty_hash が過去に記録済みなら、(a)〜(c)を満たしても落とす(新規性なし)

通過分は注目度の合成スコアの順に最大 `Thresholds.gate_max_events` 件。
**進行中シナリオの対象企業(銘柄コード)やキーワードに一致するものは、この上限の枠外で通す**
(`forced_in=True`)。落としたものも理由付きで返す(記録は呼び出し側=統括者が行う)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from assoc.events.models import GateResult

# 株価への影響が大きい種類の開示(docs/DESIGN.md §6.2)。タイトルの文言でキーワード判定する
# (TDnet の一覧には種別コードが無いため。EDINET は docTypeCode でも判定できるが、
# ここではソースによらずタイトルの文言だけで判定できるようにしておく)。
STRONG_DISCLOSURE_KEYWORDS = (
    "大量保有報告書", "変更報告書",  # EDINET
    "公開買付", "TOB",  # TOB
    "資本業務提携", "業務提携",  # 資本業務提携
    "業績予想の修正", "業績予想を修正", "上方修正", "下方修正",  # 業績予想の大幅な修正
)


@dataclass
class GateInput:
    """足切りの対象1件(events/cluster.py の Event に、注目度の値を足したもの)。"""

    event_id: str
    title: str
    novelty_hash: str
    media_count: float = 0.0
    gdelt: float = 0.0
    wiki: float = 0.0
    growth: float = 0.0  # 注目度の前日比の伸び(例:1.5 なら +50%)。無ければ 0.0
    disclosure_type: str = ""
    codes: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)


def percentile_rank(value: float, history: list[float]) -> float:
    """history の中で value 以下の割合(0〜1)。history が空なら 0.0(判定不能)を返す。"""
    if not history:
        return 0.0
    below_or_equal = sum(1 for h in history if h <= value)
    return below_or_equal / len(history)


def is_strong_disclosure(title: str) -> bool:
    return any(kw in (title or "") for kw in STRONG_DISCLOSURE_KEYWORDS)


def composite_attention(
    event: GateInput,
    *,
    media_history: list[float],
    gdelt_history: list[float],
    wiki_history: list[float],
) -> float:
    """媒体数・GDELT・Wikipedia の3指標を、それぞれの過去90日分布での百分位に直して平均する。

    スケールが大きく異なる指標(媒体数は数件、Wikipedia は数千閲覧)をそのまま足すと
    Wikipedia が支配的になるため、百分位に揃えてから合成する。
    """
    parts = [
        percentile_rank(event.media_count, media_history),
        percentile_rank(event.gdelt, gdelt_history),
        percentile_rank(event.wiki, wiki_history),
    ]
    return sum(parts) / len(parts)


def matches_active_scenarios(
    event: GateInput, *, active_codes: set[str], active_keywords: set[str]
) -> bool:
    if active_codes and (set(event.codes) & active_codes):
        return True
    if active_keywords:
        haystack = event.title + " " + " ".join(event.entities)
        if any(kw in haystack for kw in active_keywords):
            return True
    return False


def gate_events(
    events: list[GateInput],
    thresholds,
    *,
    media_history: list[float] | None = None,
    gdelt_history: list[float] | None = None,
    wiki_history: list[float] | None = None,
    growth_history: list[float] | None = None,
    seen_novelty_hashes: set[str] | None = None,
    active_codes: set[str] | None = None,
    active_keywords: set[str] | None = None,
) -> list[GateResult]:
    """イベントの一覧を足切りする。落としたものも含め、全件分の GateResult を返す。"""
    media_history = media_history or []
    gdelt_history = gdelt_history or []
    wiki_history = wiki_history or []
    growth_history = growth_history or []
    seen_novelty_hashes = seen_novelty_hashes or set()
    active_codes = active_codes or set()
    active_keywords = active_keywords or set()

    scored: list[tuple[GateInput, float, float, bool]] = []
    for e in events:
        score = composite_attention(
            e, media_history=media_history, gdelt_history=gdelt_history, wiki_history=wiki_history
        )
        growth_pct = percentile_rank(e.growth, growth_history)
        forced = matches_active_scenarios(e, active_codes=active_codes, active_keywords=active_keywords)
        scored.append((e, score, growth_pct, forced))

    results: dict[str, GateResult] = {}
    passed_not_forced: list[tuple[GateInput, float]] = []

    for e, score, growth_pct, forced in scored:
        attention = {
            "media_count": e.media_count, "gdelt": e.gdelt, "wiki": e.wiki,
            "score": score, "growth": e.growth, "growth_percentile": growth_pct,
        }
        if e.novelty_hash in seen_novelty_hashes:
            results[e.event_id] = GateResult(
                event_id=e.event_id, passed=False, reason="新規性なし(既知の事実)",
                attention=attention, forced_in=False,
            )
            continue
        meets_attention = score >= thresholds.gate_attention_pct
        meets_growth = growth_pct >= thresholds.gate_attention_pct
        meets_strong = is_strong_disclosure(e.title) or is_strong_disclosure(e.disclosure_type)
        if not (meets_attention or meets_growth or meets_strong):
            results[e.event_id] = GateResult(
                event_id=e.event_id, passed=False,
                reason="注目度・開示の種類のいずれの条件も満たさない", attention=attention, forced_in=False,
            )
            continue
        if forced:
            reason = _condition_reason(meets_attention, meets_growth, meets_strong)
            results[e.event_id] = GateResult(
                event_id=e.event_id, passed=True, reason=f"{reason}(進行中シナリオの別枠)",
                attention=attention, forced_in=True,
            )
        else:
            passed_not_forced.append((e, score))
            results[e.event_id] = GateResult(
                event_id=e.event_id, passed=True,
                reason=_condition_reason(meets_attention, meets_growth, meets_strong),
                attention=attention, forced_in=False,
            )

    # 別枠(forced_in)以外は、注目度の合成スコアの順に上限件数まで。超えた分は理由を書き換えて落とす。
    passed_not_forced.sort(key=lambda t: t[1], reverse=True)
    for e, _score in passed_not_forced[thresholds.gate_max_events :]:
        prev = results[e.event_id]
        results[e.event_id] = GateResult(
            event_id=e.event_id, passed=False,
            reason=f"{prev.reason}(上限 {thresholds.gate_max_events} 件を超過)",
            attention=prev.attention, forced_in=False,
        )

    return [results[e.event_id] for e in events]


def _condition_reason(meets_attention: bool, meets_growth: bool, meets_strong: bool) -> str:
    reasons = []
    if meets_attention:
        reasons.append("注目度が上位5%")
    if meets_growth:
        reasons.append("注目度の伸びが上位5%")
    if meets_strong:
        reasons.append("強い種類の開示")
    return " / ".join(reasons) if reasons else "該当なし"
