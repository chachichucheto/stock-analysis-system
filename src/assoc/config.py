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
    gate_max_events: int = 3             # LLM に渡すニュースイベントの上限(進行中シナリオの関係分は別枠)
    gate_max_events_cold_start: int = 10  # 履歴が無い立ち上げの間(比べる相手が無い間)のニュース上限(D36)
    gate_max_disclosure_events: int = 3  # 開示(TDnet・EDINET)の別枠の上限。ニュースと同じ枠で競わせると、
                                         # 媒体数の少ない開示が構造的に負けるため(2026-09-30 予行演習で確認。DECISIONS D34)
    # テーマ別の見出し件数の急増(events/burst.py。DECISIONS D37)
    burst_ratio: float = 2.5             # 過去の日別件数の中央値の何倍で「増えた日」とするか(辞書で上書きできる)
    burst_min_count: int = 8             # 1日の件数の下限
    burst_lookback_days: int = 28        # 中央値を取る日数
    burst_min_history_days: int = 14     # これ未満の間は、倍率を厳しくする(中央値が不安定なため)
    burst_early_days: int = 4            # 比べる日がこれ未満の間は、辞書の cold_start_count を書いたテーマだけ検知する
    burst_early_ratio_mult: float = 1.3  # 履歴が短い間(4〜13日)は、倍率をこの倍に厳しくする
    burst_recent_days: int = 7           # 「連日」を見る日数
    burst_persist_days: int = 3          # 直近でこの日数以上「増えた日」なら、当日が落ちても急増とする
    # テーマの温度の機械更新(pack/auto_temperature.py。D37)
    temp_up_excess_5d: float = 0.05      # 代表銘柄の5日の超過(中央値−TOPIX)がこれ以上 → 上昇中
    temp_up_excess_1m: float = 0.10      # 1か月の超過がこれ以上 → 上昇中
    temp_down_excess_1m: float = -0.08   # 1か月の超過がこれ以下(5日も下向き)→ 冷却中
    # 日中の速報(alerts.py。D38)
    alert_theme_day_excess: float = 0.04   # テーマの代表銘柄の当日の超過(中央値−TOPIX)がこれ以上
    alert_theme_5d_excess: float = 0.08    # 5日の超過がこれ以上
    alert_stock_day_return: float = 0.10   # 1銘柄の当日の上げがこれ以上
    # 株価の大きな動き(events/price_shock.py。D40)。報道の量では拾えない相場の急変を、株価から別枠で通す
    shock_theme_excess_5d: float = 0.08  # テーマの代表銘柄の5日の中央値が、TOPIX よりこれ以上、上下に離れた
    shock_min_stocks: int = 3            # 株価が取れた代表銘柄の数の下限
    shock_topix_day: float = 0.02        # TOPIX が直近5営業日のどこかで1日にこれ以上下げた
    shock_topix_5d: float = 0.04         # TOPIX が5営業日でこれ以上下げた
    # 原因が分からない急騰・急落(events/unexplained.py。D44)
    unexplained_min_excess: float = 0.07       # 1日のTOPIX超過がこれ以上(上下とも)
    unexplained_min_sigma: float = 3.0         # 普段の値動きの何倍か
    unexplained_min_volume_ratio: float = 2.5  # 出来高が直近20日平均の何倍か
    # 辞書に無い話題:急に増えた語(events/terms.py。D39)
    terms_min_count: int = 8             # 当日、その語を含む記事数の下限
    terms_ratio: float = 3.0             # 過去の日別記事数の中央値の何倍で急増とするか
    terms_floor: float = 2.0             # 過去に出ていない語の、中央値の代わり(少数のぶれで鳴らさないため)
    terms_min_days: int = 4              # 比べる日がこれ未満の間は検知しない
    terms_min_relevant_share: float = 0.3  # その語を含む見出しのこの割合以上に、投資との関係(社名・テーマ・事象の語)が必要
    terms_max_share: float = 0.15        # 当日の記事の何割を超えて含まれる語は、どの話題にも出る語として除く
    ignored_grace_days: int = 2          # 開始からこの営業日数未満は、反応の有無で「無視されている疑い」と判定しない(D41)
    reacted_sigma: float = 2.0         # 「反応した」:当日の TOPIX 超過が普段の値動きの2倍以上
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
    # 継続性チェック(tracking/health.py)。暫定。毎日の記録で「劣化」判定の当たりを検証してから見直す
    health_excess_floor: float = -0.05   # TOPIX 超過がこれ以下 → 劣化
    health_excess_watch: float = -0.02   # これ以下 → 注意
    health_drawdown_pt: float = 0.08     # 超過のピークからの押し戻しがこれ以上 → 劣化
    health_volume_low: float = 0.6       # 直近5日の売買代金が20日平均のこの割合未満 → 注意(関心の低下)
    health_stall_frac: float = 0.4       # 期間のこの割合を過ぎ、織り込みが進まず材料も無ければ 停滞
    health_stall_priced_in: float = 0.2
    health_news_days: int = 5            # 新しい材料を数える日数


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
