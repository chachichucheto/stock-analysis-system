"""events パッケージで共通に使うデータ構造(docs/DESIGN.md §5)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    event_id: str
    title: str
    news_ids: list[str]
    entities: list[str]
    first_observed_at: datetime
    media_count: int
    disclosure_type: str
    novelty_hash: str
    codes: list[str] = field(default_factory=list)  # 開示由来の銘柄コード(足切りの別枠判定に使う)
    disclosure_ids: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    """足切りの結果(1イベント分)。docs/DESIGN.md §6.2。記録は呼び出し側(統括者)が行う。"""

    event_id: str
    passed: bool
    reason: str
    attention: dict  # {"media_count":…, "gdelt":…, "wiki":…, "score":…, "growth":…} の内訳
    forced_in: bool = False  # 進行中シナリオの別枠で通した場合 True
