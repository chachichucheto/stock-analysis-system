"""日中の速報(`assoc alert`。毎時の収集のあとに自動で動く。docs/DECISIONS.md D38)。

D37までは、検知は夜の `prepare`(18:15)の1回だけだった。サイバー関連が連日報じられて急騰しても、
気づくのは翌日以降になる。そこで毎時、次の2つを見て、新しいものだけを通知する。

  1. 見出しの件数の急増(events/burst.py。夜の判定と同じ)
  2. テーマの代表銘柄の値動き(取引時間中は、yfinance の当日の途中経過を含む日足):
     テーマ全体の当日の超過(中央値 − TOPIX)、5日の超過、1銘柄の大きな上げ

通知は Windows のトースト(失敗しても処理は続ける)と、`data/alerts/alerts.jsonl`・`data/alerts/latest.md`。
同じ内容は同じ日に何度も通知しない(`state.json`)。件数の急増は、前回の通知から1.5倍に増えたときだけ再通知する。
これは判断ではなく**気づくための仕組み**。銘柄を選ぶのは、夜の手順(association-daily)。
"""
from __future__ import annotations

import json
import statistics
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING

from assoc.events.burst import detect_bursts
from assoc.timeutil import JST, is_business_day, to_iso, utcnow

if TYPE_CHECKING:
    from assoc.app import App

MARKET_OPEN, MARKET_CLOSE_BUFFER = time(9, 0), time(16, 30)   # 取引時間(15:30終了)+余裕。この間と引け後しばらくだけ株価を見る
RESEND_RATIO = 1.5


@dataclass
class Alert:
    key: str                  # 同じ日に重ねて通知しないための鍵
    kind: str                 # "burst" | "theme_price" | "stock_price"
    theme_id: str
    title: str
    detail: str
    level: float = 0.0        # 再通知の判定に使う大きさ(件数・超過など)
    samples: list[str] = field(default_factory=list)


def _alerts_dir(app: "App") -> Path:
    d = app.dir("alerts")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- 判定(ネットワークに依存しない部分) -----------------------------------------------------------------

def burst_alerts(app: "App", day: date, names: dict[str, str]) -> list[Alert]:
    out = []
    for b in detect_bursts(app.con, day, app.th):
        name = names.get(b.theme_id, b.theme_id)
        base = "件数の目安で判定" if b.cold_start else f"普段 {b.baseline:.0f} 件/日の {b.ratio} 倍"
        out.append(Alert(
            key=f"{day}:{b.theme_id}:burst", kind="burst", theme_id=b.theme_id, level=float(b.today_count),
            title=f"【{b.theme_id} {name}】見出しが急増 本日{b.today_count}件",
            detail=f"{base}。直近{len(b.daily)}日のうち{b.elevated_days}日で増加。日別 {[n for _d, n in b.daily]}",
            samples=[s["title"] for s in b.samples[:3]]))
    return out


def term_alerts(app: "App", day: date, limit: int = 3) -> list[Alert]:
    """辞書のどのテーマにも当たらない、急に増えた語(events/terms.py)。上位だけ速報にする。"""
    from assoc.config import REPO_ROOT
    from assoc.events.relevance import build_matcher
    from assoc.events.terms import detect_emerging_terms
    from assoc.master.names import CompanyMaster
    matcher = build_matcher(CompanyMaster.from_db(app.con).names, REPO_ROOT / "docs" / "THEMES.md")
    out = []
    for t in [t for t in detect_emerging_terms(app.con, day, app.th, matcher=matcher) if not t.covered_by][:limit]:
        out.append(Alert(key=f"{day}:term:{t.term}", kind="term", theme_id="",
                         title=f"【新しい話題】「{t.term}」が急増 本日{t.today_count}件(普段 {t.baseline}件)",
                         detail=f"辞書のどのテーマにも当たらない語。倍率 {t.ratio}", level=float(t.today_count),
                         samples=[x["title"] for x in t.samples[:3]]))
    return out


def price_alerts(day: date, theme_stocks: dict[str, list[str]], closes: dict[str, list[float]], topix: list[float],
                 th, names: dict[str, str], code_names: dict[str, str] | None = None) -> list[Alert]:
    """closes: 銘柄コード → 日足の終値(古い順。最後が当日またはその時点の値)。topix も同じ形。"""
    code_names = code_names or {}

    def ret(series: list[float], n: int):
        return series[-1] / series[-1 - n] - 1 if len(series) > n and series[-1 - n] else None

    t1, t5 = ret(topix, 1), ret(topix, 5)
    out: list[Alert] = []
    for tid, codes in theme_stocks.items():
        r1 = {c: ret(closes[c], 1) for c in codes if c in closes}
        r5 = {c: ret(closes[c], 5) for c in codes if c in closes}
        v1 = [v for v in r1.values() if v is not None]
        v5 = [v for v in r5.values() if v is not None]
        name = names.get(tid, tid)
        if len(v1) >= 2 and t1 is not None:
            ex = statistics.median(v1) - t1
            if ex >= th.alert_theme_day_excess:
                out.append(Alert(key=f"{day}:{tid}:theme_day", kind="theme_price", theme_id=tid, level=ex,
                                 title=f"【{tid} {name}】代表銘柄が本日そろって上昇 超過 {ex:+.1%}",
                                 detail=f"{len(v1)}銘柄の中央値 {statistics.median(v1):+.1%}(TOPIX {t1:+.1%})"))
        if len(v5) >= 2 and t5 is not None:
            ex = statistics.median(v5) - t5
            if ex >= th.alert_theme_5d_excess:
                out.append(Alert(key=f"{day}:{tid}:theme_5d", kind="theme_price", theme_id=tid, level=ex,
                                 title=f"【{tid} {name}】代表銘柄の5日の超過 {ex:+.1%}",
                                 detail=f"{len(v5)}銘柄の中央値 {statistics.median(v5):+.1%}(TOPIX {t5:+.1%})"))
        for c, v in r1.items():
            if v is not None and v >= th.alert_stock_day_return:
                out.append(Alert(key=f"{day}:{tid}:{c}:stock", kind="stock_price", theme_id=tid, level=v,
                                 title=f"【{tid}】{code_names.get(c, c)}({c}) 本日 {v:+.1%}",
                                 detail=f"TOPIX {t1:+.1%}" if t1 is not None else ""))
    return out


def fresh(alerts: list[Alert], state: dict) -> list[Alert]:
    """通知済みのものを除く。件数の急増は、前回の通知から RESEND_RATIO 倍に増えたときだけ再通知する。"""
    out = []
    for a in alerts:
        prev = state.get(a.key)
        if prev is None or (a.kind == "burst" and a.level >= RESEND_RATIO * prev):
            out.append(a)
    return out


# ---- 株価の取得(日中の途中経過を含む) -------------------------------------------------------------------

def market_hours(now: datetime) -> bool:
    n = now.astimezone(JST)
    return is_business_day(n.date()) and MARKET_OPEN <= n.time() <= MARKET_CLOSE_BUFFER


def fetch_closes(codes: list[str], topix_code: str, today: date) -> tuple[dict[str, list[float]], list[float]]:
    """yfinance の日足(取引時間中は、最後の行が当日の途中経過)。1回の呼び出しでまとめて取る。"""
    from datetime import timedelta
    from assoc.market.layered_prices import _download_chunk, _split_chunk, _symbol
    from assoc.netfix import ensure_ascii_ca_bundle
    ensure_ascii_ca_bundle()
    allc = list(dict.fromkeys(codes + [topix_code]))
    symbols = [_symbol(c) for c in allc]
    by = _split_chunk(_download_chunk(symbols, today - timedelta(days=14), today), symbols)
    series = {c: [float(x) for x in by[_symbol(c)]["close"].dropna()] for c in allc if _symbol(c) in by}
    return {c: v for c, v in series.items() if c != topix_code}, series.get(topix_code, [])


# ---- 通知 -------------------------------------------------------------------------------------------------

def _toast(title: str, body: str) -> None:
    """Windows のトースト通知。失敗しても無視する(記録とファイルは別に残る)。"""
    esc = lambda s: s.replace("'", "’").replace("\n", " ")[:240]
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
          f"$t.GetElementsByTagName('text').Item(0).AppendChild($t.CreateTextNode('{esc(title)}')) > $null;"
          f"$t.GetElementsByTagName('text').Item(1).AppendChild($t.CreateTextNode('{esc(body)}')) > $null;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('assoc').Show("
          "[Windows.UI.Notifications.ToastNotification]::new($t))")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=20,
                       capture_output=True)
    except Exception:
        pass


def run_alerts(app: "App", *, now: datetime | None = None, notify: bool = True, price: bool | None = None) -> list[Alert]:
    """見出しの急増と株価の動きを見て、新しいものを通知・記録する。通知した速報を返す。"""
    from assoc.pack.build import _load_themes, _theme_stock_codes
    now = now or utcnow()
    day = now.astimezone(JST).date()
    names = _load_themes()
    found = burst_alerts(app, day, names)
    try:
        found += term_alerts(app, day)
    except Exception as e:  # 語の検知が失敗しても、ほかの速報は出す
        print(f"  新語の確認をスキップ: {type(e).__name__}: {e}")
    if (market_hours(now) if price is None else price):
        try:
            stocks = _theme_stock_codes()
            closes, topix = fetch_closes(sorted({c for v in stocks.values() for c in v}), app.topix_code(), day)
            from assoc.master.names import CompanyMaster
            master = CompanyMaster.from_db(app.con).names
            cn = {c: master.get(c) or master.get(c + "0") or c for c in closes}
            found += price_alerts(day, stocks, closes, topix, app.th, names, cn)
        except Exception as e:  # 株価が取れなくても、見出しの速報は出す
            print(f"  株価の確認をスキップ: {type(e).__name__}: {e}")
    d = _alerts_dir(app)
    sp = d / "state.json"
    state = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    state = {k: v for k, v in state.items() if k.startswith(str(day))}          # 前日までの分は捨てる
    new = fresh(found, state)
    for a in new:
        state[a.key] = a.level
    sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if new:
        with (d / "alerts.jsonl").open("a", encoding="utf-8") as f:
            for a in new:
                f.write(json.dumps({"at": to_iso(now), **asdict(a)}, ensure_ascii=False) + "\n")
        lines = [f"# 速報 {now.astimezone(JST):%Y-%m-%d %H:%M}\n"]
        for a in new:
            lines += [f"## {a.title}", a.detail] + [f"- {s}" for s in a.samples] + [""]
        lines.append("夜の手順(association-daily)を待たず、すぐ見たいときは `python -m assoc prepare` のあとに実行してください。")
        (d / "latest.md").write_text("\n".join(lines), encoding="utf-8")
        if notify:
            _toast(f"連想 速報 {len(new)}件", " / ".join(a.title for a in new[:3]))
    return new
