"""合議スコア(docs/VALUE_DESIGN.md §6.4)。複数の型が重なった銘柄を優先する。

  スコア = 型の一致数(A・B・C。D は use_type_d=True のとき、A〜C のどれかと重なったときだけ+1。既定は数えない)
         + カタリスト点(Strong 2 / Medium 1 / Weak 0.5)
         − 警告 × 減点
  候補にする条件 = 除外(block)が無く、(型が2つ以上) または (型が1つ以上 かつ カタリスト点が1以上)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from assoc.value.models import Danger, ScreenResult, Snapshot
from assoc.value.thresholds import ValueThresholds

STRENGTHS = ("None", "Weak", "Medium", "Strong")


@dataclass
class Catalyst:
    types: list[str] = field(default_factory=list)
    strength: str = "None"
    timing: str = ""
    summary: str = ""
    bull_case: str = ""
    bear_case: str = ""
    confirm_next: str = ""


@dataclass
class Candidate:
    code: str
    name: str
    snapshot: Snapshot
    screens: dict[str, ScreenResult]
    dangers: list[Danger]
    catalyst: Catalyst | None = None
    types_hit: list[str] = field(default_factory=list)       # 数えた型("D" は補助として数えたときだけ入る)
    catalyst_points: float = 0.0
    score: float = 0.0
    qualifies: bool = False
    blocked: bool = False


def count_types(screens: dict[str, ScreenResult], use_d: bool = True) -> list[str]:
    """数える型。D は A〜C と重なったときだけ補助で数える。use_d=False なら D は数えない(既定の設定)。"""
    main = [k for k in ("A", "B", "C") if screens[k].hit]
    if use_d and main and screens["D"].hit:
        return main + ["D"]
    return main


def evaluate_candidate(s: Snapshot, screens: dict[str, ScreenResult], dangers: list[Danger],
                       catalyst: Catalyst | None, th: ValueThresholds) -> Candidate:
    types = count_types(screens, th.use_type_d)
    points = th.catalyst_points.get(catalyst.strength, 0.0) if catalyst else 0.0
    warns = sum(1 for d in dangers if d.severity == "warn")
    blocked = any(d.severity == "block" for d in dangers)
    score = len(types) + points - th.warn_penalty * warns
    qualifies = (not blocked) and (len(types) >= 2 or (len(types) >= 1 and points >= 1.0))
    return Candidate(code=s.code, name=s.name, snapshot=s, screens=screens, dangers=dangers, catalyst=catalyst,
                     types_hit=types, catalyst_points=points, score=score, qualifies=qualifies, blocked=blocked)


def rank(cands: list[Candidate], limit: int | None = None) -> list[Candidate]:
    """候補になったものだけを、スコアの高い順に。同点は型の数、さらにコード順(結果を毎回同じにする)。"""
    q = [c for c in cands if c.qualifies]
    q.sort(key=lambda c: (-c.score, -len(c.types_hit), c.code))
    return q[:limit] if limit else q


def prelist_for_llm(cands: list[Candidate], th: ValueThresholds) -> list[Candidate]:
    """LLM に開示を読ませる銘柄:除外されず、A〜C のどれかに該当(型の数が多い順、上限つき)。"""
    pre = [c for c in cands if not c.blocked and any(k in c.types_hit for k in ("A", "B", "C"))]
    pre.sort(key=lambda c: (-len(c.types_hit), -c.score, c.code))
    return pre[:th.llm_max_items]
