"""割安カタリスト・モデルの閾値(docs/VALUE_DESIGN.md §6)。

**すべて初期値であり、検証前の仮置き**。出典が検索結果の要約だけのものは、コメントに「△」を付けた。
運用前に `assoc value backtest` で検証し、変更したら docs/DECISIONS.md に日付と理由を残す。
config/config.yaml の value.thresholds で上書きできる(未知のキーはエラーにする)。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any

# BNF 氏の乖離率の目安(業種別の下方乖離)。△ 紹介記事の要約のみ。東証33業種の名称で書く。
DEFAULT_DEVIATION_BY_SECTOR: dict[str, float] = {
    "情報・通信業": -0.20, "電気機器": -0.20, "機械": -0.20, "精密機器": -0.20,
    "証券、商品先物取引業": -0.15, "銀行業": -0.15,
    "医薬品": -0.12, "食料品": -0.12,
}


@dataclass(frozen=True)
class ValueThresholds:
    # --- 母集団(段階1) ---
    mcap_min_yen: float = 3e9            # 30億円。連想モデルの流動性3億円より小型に寄せる
    mcap_max_yen: float = 5e10           # 500億円以下。ぱりてきさす氏の基準 △
    min_turnover_yen: float = 3e7        # 20日平均の売買代金 3,000万円。資金規模で変える(要確認)
    turnover_window: int = 20
    min_price_yen: float = 100.0

    # --- 型A 資産バリュー(かぶ1000氏・たーちゃん氏) ---
    netnet_max: float = 1.0              # ネットネット指数 < 1(かぶ1000氏の定義)
    asset_pbr_max: float = 0.5           # PBR 0.5 倍以下 △(たーちゃん氏)
    asset_equity_ratio_min: float = 0.6  # 自己資本比率 60% 以上 △(たーちゃん氏)

    # --- 型B シクリカル底(たーちゃん氏) ---
    cyc_min_fy_rows: int = 5             # 本決算の履歴が最低これだけ要る(循環の判定のため)
    cyc_min_loss_years: int = 1          # 履歴中の赤字年の下限
    cyc_min_profit_years: int = 2        # 履歴中の黒字年の下限(「赤字と黒字を繰り返す」)
    cyc_drawdown: float = -2 / 3          # ピークの 1/3 以下 △(たーちゃん氏は 1/3〜1/4)
    cyc_peak_window_days: int = 1250     # ピークを探す期間(約5年)
    cyc_equity_ratio_min: float = 0.30   # 生き残れるか:自己資本比率
    # 生き残れるか:有利子負債 <= 現預金+有価証券(コード側で固定)

    # --- 型C 成長の初動(片山氏・ぱりてきさす氏) ---
    growth_mcap_max_yen: float = 3e10    # 300億円未満(片山氏は100億円未満が中心 △)
    growth_min_yoy: float = 0.20         # 営業利益(直近12か月)の前年比 +20% 以上 △
    growth_per_max: float = 15.0         # PER 15 倍以下(「10倍前後」の余裕を持たせた仮置き)
    surge_mult: float = 2.0              # 出来高が直前の平均の2倍以上になった日がある
    surge_within_days: int = 10
    surge_avg_window: int = 20
    chase_ret60_max: float = 0.50        # 60営業日で +50% を超えて急騰済みなら初動ではない(仮置き)

    # --- 型D 売られすぎ反発(BNF 氏)。単独では候補にしない ---
    ma_window: int = 25
    deviation_default: float = -0.20     # 25日線からの下方乖離 △
    deviation_by_sector: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_DEVIATION_BY_SECTOR))

    # --- 危険信号 ---
    dilution_warn: float = 0.10          # 発行済株式数が前年から 10% 超増えた → 警告
    dilution_block: float = 0.30         # 30% 超 → 除外
    surged_ret20: float = 0.50           # 直近20営業日で +50% 超 → 警告(急騰済み)

    # --- 合議スコア ---
    catalyst_points: dict[str, float] = field(default_factory=lambda: {"Strong": 2.0, "Medium": 1.0, "Weak": 0.5, "None": 0.0})
    warn_penalty: float = 1.0
    llm_max_items: int = 20              # LLM に渡す候補の上限(量より質)
    max_ranked: int = 10                 # レポートに出す上限

    # --- 損失の限定 ---
    risk_per_trade: float = 0.0075       # 1銘柄の最大損失 = 総資金の 0.75%(0.5〜1% の中間)
    max_total_risk: float = 0.05         # 同時保有の損失合計の上限
    max_position_pct: float = 0.20       # 1銘柄の投入額の上限(総資金比)
    participation_max: float = 0.05      # 20日平均売買代金の 5% まで
    max_stop_distance: float = 0.25      # 損切り幅がこれを超える銘柄は見送り
    stop_window: int = 20
    stop_buffer: float = 0.02
    lot_size: int = 100

    # --- 過去検証 ---
    horizon_days: int = 20               # 連想モデルと同じ(最長20営業日)
    moved_excess: float = 0.10           # TOPIX 超過 +10% で「動いた」
    min_samples: int = 30                # これ未満は結論を出さない
    permutation_draws: int = 2000
    seed: int = 20261006

    def sector_deviation(self, sector33: str | None) -> float:
        return self.deviation_by_sector.get(sector33 or "", self.deviation_default)


def thresholds_from_config(cfg) -> ValueThresholds:
    """config の value.thresholds で上書きする。未知のキーはエラー(打ち間違いで気づかず初期値になるのを防ぐ)。"""
    overrides: dict[str, Any] = (cfg.section("value").get("thresholds") or {})
    valid = {f.name for f in fields(ValueThresholds)}
    unknown = sorted(set(overrides) - valid)
    if unknown:
        raise ValueError(f"value.thresholds に未知のキーがあります: {unknown}")
    return replace(ValueThresholds(), **overrides)
