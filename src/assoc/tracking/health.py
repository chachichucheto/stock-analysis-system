"""継続性チェック:ニュースが出て銘柄を選んだあと、その期待が「継続しているか」を毎日見る。

売りのサイン(sell_signals.py)は「織り込み完了間近・シナリオ崩れ・期間終盤」という**出口**の合図だけを見る。
ここでは、それより手前の**期待の健康状態**を見る。すべて Python だけで計算し、トークンは使わない。
状態が「注意」「劣化」に変わった銘柄だけを、夜の深掘り(Claude の調査)の対象にする(入力パックの `health`)。

見る項目
- 価格:起動日からの TOPIX 超過が維持されているか / 直近5営業日の方向 / 超過のピークからの押し戻し
- 関心:直近5日の売買代金 ÷ 20日の売買代金(関心が続いているか)
- 材料:直近数日に、その銘柄の新しい開示・ニュースが出ているか(止まっていないか)
- 悪材料:見出しに悪材料の語(撤回・中止・下方修正・不正など)を含む記事(**要確認の目印**であり、自動では判断しない)
- 時間:期間の進み(期間の4割を過ぎても織り込みが進まず、材料も止まっていれば「停滞」)
- シナリオ:弱体化・終了したか

状態(優先順)
  終了 > 劣化 > 織り込み進行 > 注意 > 継続

**暫定の閾値**(`config.Thresholds` の health_*)。毎日の記録(health_snapshot)を積み、あとで「劣化」判定が
実際に失敗を先行して示していたかを検証する(検証前は、判断の補助であり、評価指標には入れない)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from assoc.config import Thresholds
from assoc.market.indicators import avg_turnover, excess_return

# 悪材料の語(見出しに含まれていたら、要確認の目印にする。暫定)
RISK_WORDS = (
    "撤回", "中止", "延期", "破談", "取り下げ", "取下げ", "見送り", "不正", "不適切", "辞任", "行政処分", "課徴金",
    "訴訟", "下方修正", "減益", "赤字", "損失", "停止", "リコール", "上場廃止", "監理", "整理", "否定", "訂正",
)

継続 = "継続"
注意 = "注意"
劣化 = "劣化"
織り込み進行 = "織り込み進行"
終了 = "終了"


@dataclass
class HealthInput:
    excess: float | None = None                 # 起動日からの TOPIX 超過(小数)
    excess_change_5d: float | None = None       # 直近5営業日での超過の変化(小数の差)
    peak_excess: float | None = None            # 起動日以降の超過のピーク
    volume_ratio: float | None = None           # 5日平均売買代金 ÷ 20日平均売買代金
    priced_in: float = 0.0
    elapsed_days: int = 0
    expected_days: int = 10
    followup_items: int = 0                     # 直近の新しい開示+ニュース(重複を除く)
    risk_items: list[str] = field(default_factory=list)   # 悪材料の語を含む見出し
    scenario_weakened: bool = False
    scenario_ended: bool = False


@dataclass
class HealthResult:
    status: str
    reasons: list[str]
    metrics: dict[str, Any]


def risk_headlines(titles: list[str]) -> list[str]:
    """悪材料の語を含む見出しだけを返す。"""
    return [t for t in titles if any(w in (t or "") for w in RISK_WORDS)]


def compute_price_metrics(prices: pd.DataFrame, topix: pd.DataFrame, start: date, day: date) -> dict[str, float | None]:
    """価格・関心の指標。株価が足りないときは None を返す(判定に使わない)。"""
    out: dict[str, float | None] = {"excess": None, "excess_change_5d": None, "peak_excess": None, "volume_ratio": None}
    if prices.empty or topix.empty:
        return out
    series: list[float] = []
    for d in prices["date"]:
        if start <= d <= day:
            try:
                series.append(excess_return(prices, topix, start, d))
            except (ValueError, KeyError, IndexError, ZeroDivisionError):
                continue
    if series:
        out["excess"] = series[-1]
        out["peak_excess"] = max(series)
        if len(series) > 5:
            out["excess_change_5d"] = series[-1] - series[-6]
    try:
        long = avg_turnover(prices, day, 20)
        short = avg_turnover(prices, day, 5)
        if long and long > 0:
            out["volume_ratio"] = short / long
    except (ValueError, KeyError, IndexError, ZeroDivisionError):
        pass
    return out


def assess(inp: HealthInput, th: Thresholds) -> HealthResult:
    metrics = {"excess": inp.excess, "excess_change_5d": inp.excess_change_5d, "peak_excess": inp.peak_excess,
               "volume_ratio": inp.volume_ratio, "priced_in": inp.priced_in, "elapsed_days": inp.elapsed_days,
               "expected_days": inp.expected_days, "followup_items": inp.followup_items,
               "risk_items": len(inp.risk_items)}
    if inp.scenario_ended:
        return HealthResult(終了, ["シナリオが終了した"], metrics)

    bad: list[str] = []       # 劣化の理由
    warn: list[str] = []      # 注意の理由

    if inp.scenario_weakened:
        bad.append("シナリオが弱体化した(強化と反対の新しい事実)")
    if inp.excess is not None:
        if inp.excess <= th.health_excess_floor:
            bad.append(f"TOPIX 超過が {inp.excess:+.1%} まで下がった(基準 {th.health_excess_floor:+.0%})")
        elif inp.excess <= th.health_excess_watch:
            warn.append(f"TOPIX 超過が {inp.excess:+.1%} とマイナス")
        if inp.peak_excess is not None and inp.peak_excess - inp.excess >= th.health_drawdown_pt:
            bad.append(f"超過のピーク {inp.peak_excess:+.1%} から {inp.peak_excess - inp.excess:.1%}pt 押し戻された")
        if inp.excess_change_5d is not None and inp.excess_change_5d <= -th.health_drawdown_pt / 2:
            warn.append(f"直近5営業日で超過が {inp.excess_change_5d:+.1%} 下向き")
    if inp.volume_ratio is not None and inp.volume_ratio < th.health_volume_low:
        warn.append(f"売買代金が細っている(直近5日は20日平均の {inp.volume_ratio:.0%})")
    if inp.risk_items:
        (bad if len(inp.risk_items) >= 2 and (inp.excess is None or inp.excess < 0) else warn).append(
            f"悪材料の語を含む見出しが {len(inp.risk_items)} 件(要確認: {inp.risk_items[0][:40]})")
    stalled = (inp.expected_days > 0 and inp.elapsed_days >= inp.expected_days * th.health_stall_frac
               and inp.priced_in < th.health_stall_priced_in and inp.followup_items == 0)
    if stalled:
        warn.append(f"停滞(期間の {inp.elapsed_days}/{inp.expected_days} 日が経過、織り込み {inp.priced_in:.0%}、新しい材料なし)")

    if bad:
        return HealthResult(劣化, bad + warn, metrics)
    if inp.priced_in >= th.priced_in_near:
        return HealthResult(織り込み進行, [f"織り込み度 {inp.priced_in:.0%}(残り余地が小さい)"] + warn, metrics)
    if warn:
        return HealthResult(注意, warn, metrics)
    reasons = []
    if inp.excess is not None:
        reasons.append(f"TOPIX 超過 {inp.excess:+.1%}を維持")
    if inp.followup_items:
        reasons.append(f"新しい材料 {inp.followup_items} 件")
    return HealthResult(継続, reasons or ["異常なし"], metrics)
