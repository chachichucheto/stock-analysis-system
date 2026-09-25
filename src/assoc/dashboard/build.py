"""ダッシュボード:記録と DB から、ブラウザで開くだけで見られる1枚の HTML を作る。

python -m assoc dashboard → data/reports/dashboard.html
サーバーは不要。レポートの作成(report)と翌日の処理(nextday)のたびに作り直す。
"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assoc.state import load_scenarios
from assoc.timeutil import JST, business_days_between, utcnow

if TYPE_CHECKING:
    from assoc.app import App

CSS = """
:root {
  color-scheme: light;
  --surface-0: #f4f3ef; --surface-1: #fcfcfb; --border: #dddcd6;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #77766f;
  --accent: #2a78d6;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-0: #111110; --surface-1: #1a1a19; --border: #383835;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #9a998f; --accent: #3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #111110; --surface-1: #1a1a19; --border: #383835;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #9a998f; --accent: #3987e5;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 14px/1.6 "Hiragino Sans", "Yu Gothic UI", "Meiryo", system-ui, sans-serif; }
header { padding: 16px 24px; border-bottom: 1px solid var(--border); background: var(--surface-1);
  display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: baseline; }
header h1 { font-size: 18px; margin: 0; }
header .meta { color: var(--text-secondary); font-size: 13px; }
nav { padding: 8px 24px; display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 13px;
  background: var(--surface-1); border-bottom: 1px solid var(--border); position: sticky; top: 0; }
nav a { color: var(--accent); text-decoration: none; }
main { padding: 16px 24px 48px; max-width: 1280px; margin: 0 auto; }
section { margin-top: 28px; }
h2 { font-size: 16px; margin: 0 0 10px; }
.note { color: var(--text-muted); font-size: 12px; margin: 4px 0 10px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.tile .label { color: var(--text-secondary); font-size: 12px; }
.tile .value { font-size: 28px; font-weight: 600; line-height: 1.3; }
.tile .sub { color: var(--text-muted); font-size: 12px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
.card h3 { margin: 0 0 6px; font-size: 15px; }
.card dl { display: grid; grid-template-columns: 7.5em 1fr; gap: 2px 8px; margin: 0; font-size: 13px; }
.card dt { color: var(--text-secondary); }
.card dd { margin: 0; }
.table-wrap { overflow-x: auto; background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { color: var(--text-secondary); font-weight: 600; white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
.badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; white-space: nowrap; }
.badge::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--dot, var(--text-muted)); }
.good { --dot: var(--good); } .warning { --dot: var(--warning); }
.serious { --dot: var(--serious); } .critical { --dot: var(--critical); }
.empty { color: var(--text-muted); padding: 12px; }
.stars { letter-spacing: 1px; }
"""


def _e(x: Any) -> str:
    return escape("" if x is None else str(x))


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "―"
    return f"{x:+.0%}" if signed else f"{x:.0%}"


def _table(headers: list[str], rows: list[list[str]], num_cols: set[int] = frozenset(), empty: str = "なし") -> str:
    if not rows:
        return f'<div class="table-wrap"><div class="empty">{_e(empty)}</div></div>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f'<td class="num">{c}</td>' if i in num_cols else f"<td>{c}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _badge(level: str, label: str) -> str:
    icon = {"good": "✓", "warning": "!", "serious": "!", "critical": "✗"}.get(level, "")
    return f'<span class="badge {level}">{icon} {_e(label)}</span>'


def _tile(label: str, value: str, sub: str = "") -> str:
    return f'<div class="tile"><div class="label">{_e(label)}</div><div class="value">{_e(value)}</div>' \
           f'<div class="sub">{_e(sub)}</div></div>'


def build_dashboard(app: "App", day: date | None = None) -> Path:
    day = day or app.today()
    store, con = app.store, app.con
    scenarios = load_scenarios(store)
    snapshots = list(store.read("ranking_snapshot"))
    last_date = max((s["date"] for s in snapshots), default=None)
    latest = sorted((s for s in snapshots if s["date"] == last_date and not s.get("empty")), key=lambda s: s["rank"])
    cands = {(c["scenario_id"], c["code"]): c for c in store.read("candidate")}
    tracking = list(store.read("pick_tracking"))
    picks = [p for p in tracking if p.get("event") == "first_pick"]
    moved = {(p["scenario_id"], p["code"]): p for p in tracking if p.get("event") == "moved"}
    runs = list(store.read("run_log"))

    def name(sid: str, code: str) -> str:
        c = cands.get((sid, code), {})
        return f"{c.get('master_name') or c.get('company_name') or ''}({code})"

    # ---- 数値の要約 ----
    month = day.strftime("%Y-%m")
    month_picks = [p for p in picks if p["first_pick_date"][:7] == month]
    hit = sum(1 for p in month_picks if (p["scenario_id"], p["code"]) in moved)
    active = [s for s in scenarios.values() if s.is_active]
    recent = [r for r in runs if r.get("date", "") >= (day - timedelta(days=30)).isoformat()]
    executed = [r for r in recent if r.get("executed")]
    mins = sorted(r["duration_min"] for r in executed if r.get("duration_min") is not None)
    kpis = "".join([
        _tile("本命(最新のレポート)", str(sum(1 for s in latest if s["tier"] == "本命")), f"{last_date or 'まだありません'}"),
        _tile("進行中・待機中のシナリオ", str(len(active)), f"上限 {app.th.max_active_scenarios} 件"),
        _tile(f"今月の的中率(銘柄)", f"{hit / len(month_picks):.0%}" if month_picks else "―",
              f"本命 {len(month_picks)} 件のうち動いた {hit} 件"),
        _tile("直近30日の実行", f"{len(executed)} 日", f"作業時間の中央値 {mins[len(mins) // 2]} 分" if mins else "記録なし"),
    ])

    # ---- 本命のカード ----
    cards = []
    for s in latest:
        if s["tier"] != "本命":
            continue
        sc = scenarios.get(s["scenario_id"])
        c = cands.get((s["scenario_id"], s["code"]), {})
        if sc is None:
            continue
        plan = sc.check_plan
        verified = sum(1 for p in plan.values() if p.get("status") == "確認")
        cards.append(
            f'<div class="card"><h3>{s["rank"]}位 {_e(name(s["scenario_id"], s["code"]))} '
            f'<span class="stars">{"★" * s["stars"]}</span></h3><dl>'
            f'<dt>シナリオ</dt><dd>{_e(sc.statement)}({_e(sc.scenario_id)}・強さ {_e(sc.strength)})</dd>'
            f'<dt>連想の段数</dt><dd>{_e(c.get("stage"))}段目・{_e(c.get("viewpoint"))}の視点</dd>'
            f'<dt>自信度</dt><dd>{s["confidence"]:.3f}(実現確度 {_pct(s["realization_prob"])} × 残り余地 '
            f'{_pct(s["remaining_room"], True)} × 鮮度 {_pct(s["freshness"])})</dd>'
            f'<dt>織り込み度</dt><dd>{_pct(s["priced_in"])}</dd>'
            f'<dt>反応の診断</dt><dd>{_e(s["diagnosis"])}</dd>'
            f'<dt>裏取り</dt><dd>{verified}/{len(plan)} 確認済み</dd>'
            f'<dt>崩れる条件</dt><dd>{_e(" / ".join(c.get("break_conditions", [])))}</dd>'
            f'<dt>次の材料</dt><dd>{_e(c.get("next_catalyst"))}</dd></dl></div>')
    cards_html = f'<div class="cards">{"".join(cards)}</div>' if cards else \
        '<div class="table-wrap"><div class="empty">本命:該当なし</div></div>'

    # ---- ランキング ----
    rank_rows = [[str(s["rank"]), _e(name(s["scenario_id"], s["code"])), _e(s["tier"]),
                  f'<span class="stars">{"★" * s["stars"]}</span> {s["confidence"]:.3f}',
                  _pct(s["realization_prob"]), _pct(s["remaining_room"], True), _pct(s["freshness"]),
                  _pct(s["priced_in"]), _e(s["diagnosis"]), _e(s.get("excluded_reason") or "")] for s in latest]

    # ---- シナリオ ----
    status_level = {"進行中": "good", "待機": "warning", "休眠": "serious", "終了": "critical"}
    order = {"進行中": 0, "待機": 1, "休眠": 2, "終了": 3}
    scen_rows = []
    for sc in sorted(scenarios.values(), key=lambda s: (order.get(s.status, 9), s.scenario_id)):
        if sc.status == "終了" and sc.created_date < day - timedelta(days=45):
            continue
        remaining = "―"
        if sc.status == "進行中" and sc.clock_date:
            remaining = str(sc.expected_days - business_days_between(sc.clock_date, day))
        plan = sc.check_plan
        scen_rows.append([_e(sc.scenario_id), _e(sc.statement),
                          _badge(status_level.get(sc.status, ""), sc.status + (f":{sc.end_reason}" if sc.end_reason else "")),
                          _e(sc.time_type), _e(sc.strength), remaining,
                          f'{sum(1 for p in plan.values() if p.get("status") == "確認")}/{len(plan)}',
                          _e("、".join(f"{c.get('master_name') or c['company_name']}({c['code']})" for c in sc.candidates))])

    # ---- 売りのサイン ----
    sig_rows = [[_e(p["date"]), _e(name(p["scenario_id"], p["code"])), _e(p["scenario_id"]),
                 _badge("serious", " / ".join(p["signals"]))]
                for p in sorted((p for p in tracking if p.get("event") == "sell_signal"),
                                key=lambda p: p["date"], reverse=True)[:30]]

    # ---- 本命の履歴 ----
    hist_rows = []
    for p in sorted(picks, key=lambda p: p["first_pick_date"], reverse=True)[:100]:
        m = moved.get((p["scenario_id"], p["code"]))
        first = date.fromisoformat(p["first_pick_date"])
        window_over = business_days_between(first, day) > min(p["expected_days"], app.th.moved_max_days)
        result = _badge("good", "動いた") if m else (_badge("critical", "動かず") if window_over else _badge("warning", "判定中"))
        hist_rows.append([_e(p["first_pick_date"]), _e(name(p["scenario_id"], p["code"])), _e(p["scenario_id"]),
                          f'{_e(p.get("stage"))}段目', result,
                          _e(m.get("days_to_move")) if m else "―", _pct(m.get("max_excess"), True) if m else "―"])

    # ---- 収集の状態 ----
    fetch_rows = []
    try:
        rows = con.execute("""
            SELECT source, max(fetched_at) FILTER (WHERE ok) AS last_ok, max(fetched_at) AS last_try,
                   arg_max(items, fetched_at) AS last_items, arg_max(ok, fetched_at) AS last_is_ok,
                   arg_max(message, fetched_at) AS last_msg
            FROM fetch_log GROUP BY source ORDER BY source""").fetchall()
        from assoc.ingest.run import consecutive_failures
        for source, last_ok, last_try, items, is_ok, msg in rows:
            if consecutive_failures(con, source):
                level, label = "critical", "3回続けて失敗"
            elif not is_ok:
                level, label = "warning", "直近で失敗"
            else:
                level, label = "good", "正常"
            fmt = lambda t: t.astimezone(JST).strftime("%m/%d %H:%M") if t else "―"
            fetch_rows.append([_e(source), _badge(level, label), fmt(last_ok), fmt(last_try), _e(items), _e(msg or "")])
    except Exception as e:                 # 収集をまだ一度も動かしていない場合など
        fetch_rows = [[_e("―"), _badge("warning", "確認できません"), "―", "―", "―", _e(str(e)[:80])]]
    counts = {}
    for table in ("news_item", "disclosure", "universe_daily"):
        try:
            counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except Exception:
            counts[table] = "―"

    # ---- 実行記録 ----
    run_rows = [[_e(r["date"]), _badge("good", "実行") if r.get("executed") else _badge("warning", "見送り"),
                 _e(r.get("mode") or ""), _e(r.get("duration_min") if r.get("duration_min") is not None else "―"),
                 _e(r.get("skip_reason") or "")] for r in sorted(runs, key=lambda r: r["date"], reverse=True)[:20]]

    # ---- 今日の結論(最新のレポートへのリンク) ----
    report_link = f'report_{last_date}.md' if last_date else None
    generated = utcnow().astimezone(JST).strftime("%Y-%m-%d %H:%M")

    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>連想ダッシュボード</title><style>{CSS}</style></head>
<body>
<header><h1>連想ダッシュボード</h1>
<span class="meta">作成 {generated} / 最新のレポート {_e(last_date or 'まだありません')}
{f' / <a href="{_e(report_link)}">連想レポートを開く</a>' if report_link else ''}</span></header>
<nav><a href="#summary">要約</a><a href="#picks">本命</a><a href="#ranking">ランキング</a><a href="#scenarios">シナリオ</a>
<a href="#signals">売りのサイン</a><a href="#history">本命の履歴</a><a href="#collect">収集の状態</a><a href="#runs">実行記録</a></nav>
<main>
<section id="summary"><h2>要約</h2><div class="kpis">{kpis}</div>
<p class="note">的中率は改善のための目安。合否の判定は運用開始から約6か月後にまとめて行う(CONCEPT §11)。</p></section>
<section id="picks"><h2>本命</h2>{cards_html}</section>
<section id="ranking"><h2>自信度ランキング({_e(last_date or '―')})</h2>
<p class="note">自信度 = 実現確度 × 残り余地 × 鮮度。上位ほど自信がある。</p>
{_table(["順位", "銘柄", "区分", "自信度", "実現確度", "残り余地", "鮮度", "織り込み", "診断", "本命にしない理由"],
        rank_rows, {0, 4, 5, 6, 7}, "この日の候補はありません" if last_date else "まだランキングがありません")}</section>
<section id="scenarios"><h2>シナリオ</h2>
<p class="note">終了したシナリオは45日分だけ表示する。</p>
{_table(["番号", "シナリオ", "状態", "時間の型", "強さ", "残り(営業日)", "裏取り", "候補"], scen_rows, {5, 6},
        "まだシナリオがありません")}</section>
<section id="signals"><h2>売りのサイン(過去に本命とした銘柄)</h2>
{_table(["日付", "銘柄", "シナリオ", "サイン"], sig_rows, empty="売りのサインはありません")}</section>
<section id="history"><h2>本命の履歴</h2>
<p class="note">「動いた」:本命に挙げた日の終値から、想定期間内(最長20営業日)に TOPIX より +10% 以上上回った。</p>
{_table(["本命にした日", "銘柄", "シナリオ", "段数", "結果", "動くまで(営業日)", "最大の超過上昇"], hist_rows, {5, 6},
        "まだ本命がありません")}</section>
<section id="collect"><h2>収集の状態</h2>
<p class="note">ニュース {_e(counts['news_item'])} 件 / 開示 {_e(counts['disclosure'])} 件 / 上場銘柄一覧 {_e(counts['universe_daily'])} 行</p>
{_table(["収集器", "状態", "最後に成功", "最後に実行", "件数(直近)", "メッセージ"], fetch_rows, {4},
        "まだ収集していません(python -m assoc collect)")}</section>
<section id="runs"><h2>実行記録(夜の手順)</h2>
{_table(["日付", "実行", "手順", "所要(分)", "見送った理由"], run_rows, {3}, "まだ記録がありません")}</section>
</main></body></html>
"""
    path = app.dir("reports") / "dashboard.html"
    path.write_text(html, encoding="utf-8")
    return path
