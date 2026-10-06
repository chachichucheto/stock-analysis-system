"""4つの型のスクリーン(docs/VALUE_DESIGN.md §6.2)。いずれも純粋な関数で、根拠の数字を返す。

データが足りないときは「該当しない」とし、足りない項目を missing に書く(推測で通さない)。
"""
from __future__ import annotations

from assoc.value import metrics
from assoc.value.models import ScreenResult, Snapshot
from assoc.value.thresholds import ValueThresholds


def _pct(x: float | None) -> str:
    return "不明" if x is None else f"{x * 100:.1f}%"


def screen_a(s: Snapshot, th: ValueThresholds) -> ScreenResult:
    """型A 資産バリュー:ネットネット指数<1、または PBR<0.5 かつ 自己資本比率≧60%。"""
    r = ScreenResult("A", False)
    f = s.fin
    if f is None or s.mcap is None:
        r.missing.append("財務データ/時価総額")
        return r
    nn = metrics.netnet_index(s.mcap, f)
    nn_c = metrics.netnet_index(s.mcap, f, metrics.CONSERVATIVE)
    p, eq = metrics.pbr(s.mcap, f), metrics.equity_ratio(f)
    la = metrics.liquid_assets(f)
    r.facts = {"netnet": nn, "netnet_conservative": nn_c, "pbr": p, "equity_ratio": eq,
               "liquid_assets": la, "total_liabilities": f.total_liabilities, "mcap": s.mcap}
    if nn is None and la is None:
        r.missing.append("流動資産の内訳(現預金・売掛金)")
    by_netnet = nn is not None and nn < th.netnet_max
    by_pbr = p is not None and eq is not None and p < th.asset_pbr_max and eq >= th.asset_equity_ratio_min
    r.hit = by_netnet or by_pbr
    r.reasons.append(f"ネットネット指数 {'なし(分母が0以下か欠損)' if nn is None else f'{nn:.2f}'}(1未満で該当)")
    r.reasons.append(f"PBR {'不明' if p is None else f'{p:.2f}'}倍・自己資本比率 {_pct(eq)}"
                     f"(PBR<{th.asset_pbr_max} かつ {th.asset_equity_ratio_min:.0%}以上で該当)")
    r.facts["by"] = "netnet" if by_netnet else ("pbr" if by_pbr else None)
    return r


def screen_b(s: Snapshot, th: ValueThresholds) -> ScreenResult:
    """型B シクリカル底:循環(赤字と黒字の履歴)× いま赤字 × 株価がピークの1/3以下 × 生き残れる。"""
    r = ScreenResult("B", False)
    f = s.fin
    if f is None:
        r.missing.append("財務データ")
        return r
    hist = s.fy_history
    if len(hist) < th.cyc_min_fy_rows:
        r.missing.append(f"本決算の履歴 {th.cyc_min_fy_rows}期分(実際 {len(hist)}期)")
        return r
    stats = metrics.fy_stats(hist)
    if stats is None:
        r.missing.append("本決算の営業利益")
        return r
    dd = metrics.drawdown_from_peak(s.prices, th.cyc_peak_window_days)
    eq = metrics.equity_ratio(f)
    cashlike = metrics.liquid_cash_like(f)
    survive_debt = (f.interest_debt is not None and cashlike is not None and f.interest_debt <= cashlike)
    if f.interest_debt is None:
        r.missing.append("有利子負債")
    if eq is None:
        r.missing.append("自己資本比率")
    cyclical = stats["loss_years"] >= th.cyc_min_loss_years and stats["profit_years"] >= th.cyc_min_profit_years
    in_loss = (f.operating_income_ttm is not None and f.operating_income_ttm < 0) or stats["consecutive_loss_years"] >= 1
    deep = dd is not None and dd <= th.cyc_drawdown
    survives = eq is not None and eq >= th.cyc_equity_ratio_min and survive_debt
    r.facts = {**stats, "drawdown": dd, "equity_ratio": eq, "interest_debt": f.interest_debt, "cash_like": cashlike}
    r.hit = cyclical and in_loss and deep and survives
    r.reasons += [
        f"循環:本決算{stats['years']}期で赤字{stats['loss_years']}・黒字{stats['profit_years']}"
        f"({'該当' if cyclical else '非該当'})",
        f"いま赤字:直近12か月の営業利益 {'不明' if f.operating_income_ttm is None else f'{f.operating_income_ttm / 1e8:.1f}億円'}"
        f"・連続赤字 {stats['consecutive_loss_years']}期({'該当' if in_loss else '非該当'})",
        f"株価:ピークから {_pct(dd)}({th.cyc_drawdown:.0%}以下で該当)",
        f"生存性:自己資本比率 {_pct(eq)}・有利子負債≦現預金+有価証券 {'はい' if survive_debt else 'いいえ/不明'}",
    ]
    return r


def screen_c(s: Snapshot, th: ValueThresholds) -> ScreenResult:
    """型C 成長の初動:小型 × 営業利益の前年比が+20%以上かつ加速 × PERが高すぎない × 出来高が増え始め × 急騰済みでない。"""
    r = ScreenResult("C", False)
    f = s.fin
    if f is None or s.mcap is None:
        r.missing.append("財務データ/時価総額")
        return r
    g_latest = metrics.yoy(f.operating_income_ttm, s.fin_year_ago.operating_income_ttm if s.fin_year_ago else None)
    g_prev = None
    if s.fin_prev is not None and s.fin_prev_year_ago is not None:
        g_prev = metrics.yoy(s.fin_prev.operating_income_ttm, s.fin_prev_year_ago.operating_income_ttm)
    p = metrics.per(s.mcap, f)
    surge = metrics.volume_surge(s.prices, th.surge_mult, th.surge_within_days, th.surge_avg_window)
    ret60 = metrics.ret_over(s.prices, 60)
    for name, v in (("前年同期の営業利益", g_latest), ("1つ前の期の前年比", g_prev), ("PER(黒字)", p),
                    ("出来高の履歴", surge), ("60日の株価", ret60)):
        if v is None:
            r.missing.append(name)
    r.facts = {"growth_latest": g_latest, "growth_prev": g_prev, "per": p, "volume_surge": surge,
               "ret60": ret60, "mcap": s.mcap}
    if r.missing:
        r.reasons.append("データ不足のため該当にしない: " + "、".join(r.missing))
        return r
    small = s.mcap <= th.growth_mcap_max_yen
    growing = g_latest >= th.growth_min_yoy and g_latest > g_prev
    cheap = p <= th.growth_per_max
    not_chasing = ret60 <= th.chase_ret60_max
    r.hit = small and growing and cheap and bool(surge) and not_chasing
    r.reasons += [
        f"時価総額 {s.mcap / 1e8:.0f}億円(上限{th.growth_mcap_max_yen / 1e8:.0f}億円:{'○' if small else '×'})",
        f"営業利益(12か月)の前年比 {_pct(g_latest)}(前の期 {_pct(g_prev)}。{th.growth_min_yoy:.0%}以上かつ加速:{'○' if growing else '×'})",
        f"PER {p:.1f}倍(上限{th.growth_per_max:.0f}倍:{'○' if cheap else '×'})",
        f"出来高が{th.surge_mult:.0f}倍以上の日が直近{th.surge_within_days}日に{'あり' if surge else 'なし'}",
        f"60日の騰落 {_pct(ret60)}(上限{th.chase_ret60_max:.0%}:{'○' if not_chasing else '×'})",
    ]
    return r


def screen_d(s: Snapshot, th: ValueThresholds) -> ScreenResult:
    """型D 売られすぎ反発:25日線からの下方乖離が業種別の閾値以下。単独では候補にしない(score で制限)。"""
    r = ScreenResult("D", False)
    dev = metrics.ma_deviation(s.prices, th.ma_window)
    limit = th.sector_deviation(s.sector33)
    r.facts = {"deviation": dev, "limit": limit, "sector33": s.sector33}
    if dev is None:
        r.missing.append(f"{th.ma_window}日分の株価")
        return r
    r.hit = dev <= limit
    r.reasons.append(f"{th.ma_window}日線からの乖離 {_pct(dev)}(業種「{s.sector33 or '不明'}」の閾値 {limit:.0%}以下で該当)")
    return r


def run_screens(s: Snapshot, th: ValueThresholds) -> dict[str, ScreenResult]:
    return {"A": screen_a(s, th), "B": screen_b(s, th), "C": screen_c(s, th), "D": screen_d(s, th)}
