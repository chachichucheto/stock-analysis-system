"""割安カタリスト・モデルのデータ構造(docs/VALUE_DESIGN.md §4)。金額の単位はすべて円。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

PERIODS = ("FY", "Q1", "Q2", "Q3")


@dataclass(frozen=True)
class FinancialRow:
    """1回の決算開示ぶん。disclosed_date(開示日)が過去検証の先読み防止の要。

    貸借対照表の項目はその期末の値、損益の項目は**直近12か月(TTM)**の値。
    """
    code: str
    period_end: date
    disclosed_date: date
    period: str                              # FY | Q1 | Q2 | Q3
    cash: float | None = None                # 現金及び預金
    receivables: float | None = None         # 受取手形及び売掛金
    securities: float | None = None          # 有価証券(流動資産)
    investment_securities: float | None = None   # 投資有価証券
    allowance: float | None = None           # 貸倒引当金(正の数)
    current_assets: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    equity: float | None = None              # 純資産(自己資本)
    interest_debt: float | None = None       # 有利子負債
    revenue_ttm: float | None = None
    operating_income_ttm: float | None = None
    net_income_ttm: float | None = None
    shares_ex_treasury: float | None = None  # 発行済株式数(自己株式を除く)


@dataclass(frozen=True)
class UniverseRow:
    code: str
    name: str
    market: str
    sector33: str
    listed_date: date | None
    delisted_date: date | None = None
    monitoring: bool = False                 # 監理・整理(現在の状態。過去検証では使わない)
    going_concern: bool = False              # 継続企業の疑義(同上)


@dataclass
class Snapshot:
    """ある日(asof)の時点で知り得る情報の束。prices は asof 以前だけ(先読み防止を構造で守る)。"""
    code: str
    name: str
    sector33: str
    asof: date
    prices: pd.DataFrame                     # date <= asof のみ
    fin: FinancialRow | None                 # disclosed_date < asof の最新の期
    fin_year_ago: FinancialRow | None        # fin の約1年前の期
    fin_prev: FinancialRow | None            # fin の1つ前の期
    fin_prev_year_ago: FinancialRow | None   # fin_prev の約1年前の期
    fy_history: list[FinancialRow]           # 本決算の履歴(新しい順、同じ期は最新の開示版)
    price: float = 0.0
    mcap: float | None = None
    turnover: float | None = None


@dataclass
class ScreenResult:
    kind: str                                # "A" | "B" | "C" | "D"
    hit: bool
    facts: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)   # 該当/非該当の根拠(数字つき)
    missing: list[str] = field(default_factory=list)   # 足りなかったデータ。足りないときは該当にしない


@dataclass(frozen=True)
class Danger:
    severity: str                            # "block"(除外) | "warn"(警告)
    text: str
