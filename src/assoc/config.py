"""設定の読み込み。閾値は docs/DESIGN.md §7 の初期値を持ち、ANALYSIS_PLAN.md で固定する。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Thresholds:
    """docs/DESIGN.md §7 の閾値。変更したら DECISIONS.md に日付と理由を残す。"""

    moved_excess: float = 0.10           # 「動いた」:TOPIX 超過 +10%
    move_start_excess: float = 0.05      # 「動き出した日」:TOPIX 超過 +5%
    moved_max_days: int = 20             # 「動いた」を見る最長の営業日数
    gate_attention_pct: float = 0.95     # 足切り:過去90日のイベントの上位5%
    gate_lookback_days: int = 90
    gate_max_events: int = 3             # LLM に渡すイベントの上限(進行中シナリオの関係分は別枠)
    reacted_sigma: float = 2.0           # 「反応した」:当日の TOPIX 超過が普段の値動きの2倍以上
    attention_high_pct: float = 0.80     # 注目度が「高い」:過去90日の上位20%
    max_candidates_per_scenario: int = 5
    max_picks: int = 3
    max_active_scenarios: int = 15
    default_expected_days: int = 10
    min_expected_days: int = 5
    max_expected_days: int = 20
    priced_in_done: float = 0.80         # 織り込み完了
    priced_in_near: float = 0.70         # 売りのサイン:完了が近い
    ending_soon_days: int = 2            # 売りのサイン:想定期間の残り
    not_started_days: int = 60           # 待機のまま60営業日で終了
    min_turnover_yen: float = 3e8        # 流動性の下限:20日平均の売買代金3億円(2026-09-25 決定。DECISIONS D33)
    min_price_yen: float = 100.0         # 極端な低位株
    surged_return: float = 0.50          # 直近20営業日で +50%超は急騰済み
    surged_days: int = 20
    vol_window: int = 20                 # 普段の値動きを測る日数
    star_thresholds: tuple[float, float, float] = (0.05, 0.10, 0.20)  # ★2〜★4 の自信度の下限(仮。S2-d で決める)


@dataclass
class Config:
    raw: dict[str, Any]
    thresholds: Thresholds = field(default_factory=Thresholds)

    def path(self, key: str) -> Path:
        value = self.raw.get("paths", {}).get(key, "")
        if not value:
            return Path()
        p = Path(value)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def data_dir(self) -> Path:
        return self.path("data_dir") if self.raw.get("paths", {}).get("data_dir") else REPO_ROOT / "data"

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {}) or {}


def load_config(path: str | Path | None = None) -> Config:
    """config/config.yaml を読む。無ければ見本(config.example.yaml)を使う。"""
    candidates = [Path(path)] if path else [REPO_ROOT / "config" / "config.yaml",
                                           REPO_ROOT / "config" / "config.example.yaml"]
    for p in candidates:
        if p.exists():
            with p.open(encoding="utf-8") as f:
                return Config(raw=yaml.safe_load(f) or {})
    raise FileNotFoundError(f"設定ファイルが見つかりません: {candidates}")
