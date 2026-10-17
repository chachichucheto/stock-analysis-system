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
  color-scheme: dark;
  --page: #0d0d0d; --surface-0: #0d0d0d; --surface-1: #1a1a19; --surface-2: #222220;
  --border: rgba(255,255,255,0.10); --border-strong: rgba(255,255,255,0.18);
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
  --accent: #3987e5; --accent-strong: #1c5cab;
  --good: #1baf7a; --warning: #eab308; --serious: #ec835a; --critical: #e66767;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 20px; max-width: 1200px; background: var(--page); color: var(--text-primary);
  font: 14px/1.6 system-ui, -apple-system, "Segoe UI", "Yu Gothic", "Meiryo", sans-serif; }
.model-nav { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
.model-nav a { padding: 7px 16px; border-radius: 6px; background: var(--surface-1); border: 1px solid var(--border);
  color: var(--text-secondary); text-decoration: none; font-size: 13px; }
.model-nav a.active { background: var(--accent); color: white; border-color: var(--accent); font-weight: bold; }
header { padding: 0 0 6px; display: flex; flex-wrap: wrap; gap: 4px 24px; align-items: baseline; }
header h1 { font-size: 24px; margin: 0; }
header .meta { color: var(--text-muted); font-size: 13px; }
nav { padding: 8px 0; display: flex; flex-wrap: wrap; gap: 6px; font-size: 13px;
  background: var(--page); position: sticky; top: 0; z-index: 5; }
nav a { padding: 5px 12px; border-radius: 6px; background: var(--surface-1); border: 1px solid var(--border);
  color: var(--text-secondary); text-decoration: none; }
nav a:hover { border-color: var(--accent); color: var(--text-primary); }
main { padding: 0 0 48px; }
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
th { color: var(--text-secondary); font-weight: 600; white-space: nowrap; background: var(--surface-2); }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
.badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; white-space: nowrap; }
.badge::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--dot, var(--text-muted)); }
.good { --dot: var(--good); } .warning { --dot: var(--warning); }
.serious { --dot: var(--serious); } .critical { --dot: var(--critical); }
.empty { color: var(--text-muted); padding: 12px; }
.stars { letter-spacing: 1px; }
a { color: var(--accent); }
.main-tabs { display: flex; gap: 6px; margin: 4px 0 14px; border-bottom: 1px solid var(--border); }
.main-tab-btn { padding: 8px 18px; cursor: pointer; border: 1px solid var(--border); border-bottom: none;
  border-radius: 6px 6px 0 0; background: var(--surface-1); color: var(--text-secondary); font-size: 13px; }
.main-tab-btn.active { background: var(--accent); color: white; border-color: var(--accent); font-weight: bold; }
.newsbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
.newsbar input, .newsbar select { background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.newsbar input[type=text] { width: 260px; }
.newsbar button { background: var(--surface-1); color: var(--text-secondary); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 13px; }
.newsbar button:hover { border-color: var(--accent); color: var(--text-primary); }
.kindtag { display: inline-block; padding: 0 6px; border-radius: 4px; font-size: 11px; background: var(--surface-2);
  border: 1px solid var(--border); color: var(--text-secondary); white-space: nowrap; }
.kindtag.disc { color: var(--warning); }
#news-table td.t { white-space: nowrap; color: var(--text-muted); }

/* ---- D44:タブ分け・色づけ ---- */
:root { --c-judge: #4c9aff; --c-check: #f5a524; --c-system: #2fc487; --c-news: #b794f6;
  --gold: #fbbf24; --pos: #34d399; --neg: #f87171; --info: #60a5fa; }
.main-tabs { gap: 8px; margin: 8px 0 18px; border-bottom: 2px solid var(--border-strong); }
.main-tab-btn { padding: 10px 22px; font-size: 15px; font-weight: 600; border-width: 1px; border-bottom: none;
  border-top: 4px solid var(--tc, var(--border)); color: var(--text-secondary); }
.main-tab-btn:hover { background: var(--surface-2); color: var(--text-primary); }
.main-tab-btn.active { background: var(--tc); color: #0b0b0b; border-color: var(--tc); }
.main-tab-btn small { font-weight: 400; font-size: 11px; opacity: .85; margin-left: 6px; }
.tab-judge { --tc: var(--c-judge); } .tab-check { --tc: var(--c-check); }
.tab-system { --tc: var(--c-system); } .tab-news { --tc: var(--c-news); }
.tabpane section h2 { border-left: 6px solid var(--tc); padding-left: 10px; font-size: 17px; }
.tabpane nav a { border-color: var(--tc); }
.tabpane nav a:hover { background: var(--tc); color: #0b0b0b; }
.tile { border-left: 5px solid var(--tc, var(--accent)); }
.tile .value { color: var(--text-primary); }
.card { border-top: 3px solid var(--tc, var(--accent)); }
.card.pick { border-top-color: var(--good); }
.card.caution { border-top-color: var(--critical); }
tbody tr:nth-child(even) td { background: rgba(255,255,255,0.025); }
tbody tr:hover td { background: rgba(76,154,255,0.10); }
th { background: #2a2a28; color: #e5e4da; }
.pos { color: var(--pos); } .neg { color: var(--neg); } .muted { color: var(--text-muted); }
.stars { color: var(--gold); }
.tier { display: inline-block; padding: 1px 10px; border-radius: 10px; font-size: 12px; font-weight: 700; color: #0b0b0b; }
.tier.pick { background: var(--good); } .tier.watch { background: var(--gold); } .tier.caution { background: var(--critical); }
.scorebar { display: inline-block; vertical-align: middle; width: 84px; height: 9px; border-radius: 5px;
  background: rgba(255,255,255,0.12); margin-right: 8px; overflow: hidden; }
.scorebar i { display: block; height: 100%; background: linear-gradient(90deg, #2563eb, #34d399); }
.scorebar.caution i { background: linear-gradient(90deg, #b45309, #f87171); }
.score { font-weight: 700; font-variant-numeric: tabular-nums; }
.badge.info { --dot: var(--info); } .badge.muted { --dot: var(--text-muted); }
.help { background: var(--surface-1); border: 1px solid var(--border); border-left: 5px solid var(--c-judge);
  border-radius: 8px; padding: 10px 14px; margin: 8px 0 12px; font-size: 13px; line-height: 1.7; }
.help b { color: var(--gold); }
.verdict { font-size: 16px; padding: 12px 16px; margin: 6px 0 14px; border-radius: 8px;
  background: var(--surface-1); border: 1px solid var(--border); border-left: 6px solid var(--good); }
.verdict.none { border-left-color: var(--text-muted); }
.verdict b { font-size: 20px; }
"""


def _site_nav(app: "App", out_dir: Path, in_folder: bool = False) -> str:
    """既存システムのダッシュボードと同じ共通ナビゲーション(`model-nav`)を作る。

    リンクの並びと表示名は、既存フォルダの最初のページの `model-nav` から写す(既存のコードは読まない)。
    「モデル⑧ ニュース連想」(このページ)が無ければ最後に足す。設定 `paths.existing_dashboards` が無い、
    またはページが読めなければ、このページだけにする。
    """
    import html as _html
    import os
    import re
    from urllib.parse import quote

    directory = app.cfg.raw.get("paths", {}).get("existing_dashboards") or ""
    items: list[tuple[str, str]] = []
    if directory and Path(directory).is_dir():
        for p in sorted(Path(directory).glob("モデル*.html")):
            if "旧" in p.name or p.name == MIRROR_NAME:
                continue
            try:
                with p.open(encoding="utf-8") as f:
                    head = f.read(400_000)
            except OSError:
                continue
            m = re.search(r'<div class="model-nav">(.*?)</div>', head, re.S)
            if not m:
                continue
            for href, label in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', m.group(1)):
                if "連想モデル" in label or "dashboard.html" in href or href == MIRROR_NAME:
                    continue
                items.append((_html.unescape(href), _html.unescape(label)))
            break
    links = []
    for href, label in items:
        if in_folder:
            target = quote(href, safe="/")
        else:
            target = quote(Path(os.path.relpath(Path(directory) / href, out_dir)).as_posix(), safe="/")
        links.append(f'<a href="{target}">{_e(label)}</a>')
    links.append(f'<a class="active" href="{quote(MIRROR_NAME) if in_folder else "#"}">モデル⑧ ニュース連想</a>')
    return '<div class="model-nav">' + "".join(links) + "</div>"


NEWS_DAYS = 7   # ニュース一覧に載せる日数(取得した時刻で数える)


def _news_payload(app: "App") -> tuple[str, int]:
    """ニュース・開示の一覧を、画面側で絞り込めるように JSON にして返す(</script> を壊さない形)。

    1件 = [時刻(日本時間 MM-DD HH:MM), 種類(0=ニュース, 1=開示), 媒体, 見出し, URL, 銘柄コード]
    """
    import json

    from assoc.events.cluster import media_of

    since = utcnow() - timedelta(days=NEWS_DAYS)
    rows: list[tuple[Any, list]] = []
    for pub, source, summary, title, url in app.con.execute(
            "SELECT published_at, source, summary, title, url FROM news_item WHERE first_observed_at >= ? "
            "ORDER BY published_at DESC", [since]).fetchall():
        rows.append((pub, [pub.astimezone(JST).strftime("%m-%d %H:%M"), 0,
                           media_of(source or "", summary or "") or (source or ""), title or "", url or "", ""]))
    for pub, source, code, name, title, url in app.con.execute(
            "SELECT published_at, source, code, company_name, title, url FROM disclosure WHERE first_observed_at >= ? "
            "ORDER BY published_at DESC", [since]).fetchall():
        rows.append((pub, [pub.astimezone(JST).strftime("%m-%d %H:%M"), 1, source or "",
                           (f"{name} " if name else "") + (title or ""), url or "", code or ""]))
    rows.sort(key=lambda r: r[0], reverse=True)
    body = json.dumps([r[1] for r in rows], ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return body, len(rows)


NEWS_JS = """
(function () {
  var D = window.NEWS_DATA || [], PAGE = 200, page = 0, shown = [];
  var $ = function (id) { return document.getElementById(id); };
  function esc(t) { return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  var media = {}; D.forEach(function (r) { if (r[1] === 0 && r[2]) media[r[2]] = (media[r[2]] || 0) + 1; });
  var ms = Object.keys(media).sort(function (a, b) { return media[b] - media[a]; });
  $('nf-media').innerHTML = '<option value="">媒体: すべて</option>' + ms.slice(0, 80).map(function (m) {
    return '<option value="' + esc(m) + '">' + esc(m) + ' (' + media[m] + ')</option>'; }).join('');
  var days = {}; D.forEach(function (r) { days[r[0].slice(0, 5)] = 1; });
  $('nf-day').innerHTML = '<option value="">日付: すべて</option>' + Object.keys(days).sort().reverse().map(function (d) {
    return '<option value="' + d + '">' + d + '</option>'; }).join('');
  function apply() {
    var q = $('nf-q').value.trim().toLowerCase(), k = $('nf-kind').value, m = $('nf-media').value, d = $('nf-day').value;
    var words = q ? q.split(/\\s+/) : [];
    shown = D.filter(function (r) {
      if (k !== '' && String(r[1]) !== k) return false;
      if (m && r[2] !== m) return false;
      if (d && r[0].slice(0, 5) !== d) return false;
      if (words.length) { var h = (r[3] + ' ' + r[2] + ' ' + r[5]).toLowerCase();
        for (var i = 0; i < words.length; i++) if (h.indexOf(words[i]) < 0) return false; }
      return true;
    });
    page = 0; render();
  }
  function render() {
    var from = page * PAGE, rows = shown.slice(from, from + PAGE);
    $('nt-body').innerHTML = rows.map(function (r) {
      var tag = r[1] === 1 ? '<span class="kindtag disc">開示</span>' : '<span class="kindtag">ニュース</span>';
      var link = r[4] ? '<a href="' + esc(r[4]) + '" target="_blank" rel="noopener noreferrer">' + esc(r[3]) + '</a>' : esc(r[3]);
      return '<tr><td class="t">' + esc(r[0]) + '</td><td>' + tag + '</td><td>' + esc(r[2]) + '</td><td>' + link + '</td><td>' + esc(r[5]) + '</td></tr>';
    }).join('') || '<tr><td colspan="5" class="empty">該当するニュースはありません</td></tr>';
    var last = Math.max(0, Math.ceil(shown.length / PAGE) - 1);
    $('nf-count').textContent = shown.length + ' 件(全 ' + D.length + ' 件中)';
    $('nf-pos').textContent = (page + 1) + ' / ' + (last + 1);
    $('nf-prev').disabled = page <= 0; $('nf-next').disabled = page >= last;
  }
  ['nf-q', 'nf-kind', 'nf-media', 'nf-day'].forEach(function (id) { $(id).addEventListener(id === 'nf-q' ? 'input' : 'change', apply); });
  $('nf-prev').onclick = function () { page--; render(); window.scrollTo(0, 0); };
  $('nf-next').onclick = function () { page++; render(); window.scrollTo(0, 0); };
  $('nf-reset').onclick = function () { $('nf-q').value = ''; $('nf-kind').value = ''; $('nf-media').value = ''; $('nf-day').value = ''; apply(); };
  apply();
})();
var TABS = ['judge', 'check', 'system', 'news'];
function showMainTab(name) {
  if (TABS.indexOf(name) < 0) name = 'judge';
  TABS.forEach(function (t) {
    document.getElementById('tab-' + t).hidden = (t !== name);
    document.getElementById('btn-' + t).classList.toggle('active', t === name);
  });
  try { history.replaceState(null, '', '#' + name); } catch (e) {}
  window.scrollTo(0, 0);
}
(function () {
  var h = (location.hash || '').replace('#', '');
  if (TABS.indexOf(h) >= 0) { showMainTab(h); return; }
  // 旧いリンク(#picks など)が来たときは、その節があるタブを開く
  var el = h && document.getElementById(h);
  var pane = el && el.closest('.tabpane');
  if (pane) { showMainTab(pane.id.replace('tab-', '')); el.scrollIntoView(); }
})();
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
    icon = {"good": "✓", "warning": "!", "serious": "!", "critical": "✗", "info": "i"}.get(level, "")
    return f'<span class="badge {level}">{icon} {_e(label)}</span>'


def _signed(x: float | None, digits: int = 0) -> str:
    """符号つきの割合を、プラスは緑・マイナスは赤で表示する。"""
    if x is None:
        return '<span class="muted">―</span>'
    cls = "pos" if x > 0 else ("neg" if x < 0 else "muted")
    return f'<span class="{cls}">{x:+.{digits}%}</span>'


def _tier_label(tier: str, side: str | None, excluded: str | None) -> str:
    """区分の表示。売り方向の候補は『警戒』(下がる見込み)として、本命・監視と色を分ける。"""
    if side == "caution" or (excluded or "").startswith("売り方向"):
        return '<span class="tier caution">警戒</span>'
    return '<span class="tier pick">本命</span>' if tier == "本命" else '<span class="tier watch">監視</span>'


def _score_cell(confidence: float, stars: int, caution: bool, top: float) -> str:
    """自信度:棒の長さは『★4の下限』を満点とした割合。数字は見込める上昇幅(%)。"""
    width = max(2, min(100, round(100 * confidence / top))) if top else 2
    return (f'<span class="scorebar{" caution" if caution else ""}"><i style="width:{width}%"></i></span>'
            f'<span class="score">{confidence:.1%}</span> <span class="stars">{"★" * stars}</span>')


_DIAG_LEVEL = {"早耳の先回り": "good", "時間差あり": "info", "織り込み進行": "warning",
               "⚠ 無視されている疑い": "critical", "未発見": "muted", "判定前(開始直後)": "info"}


def _diag(label: str) -> str:
    return _badge(_DIAG_LEVEL.get(label, "muted"), label)


def _tile(label: str, value: str, sub: str = "", color: str = "") -> str:
    style = f' style="border-left-color:{color}"' if color else ""
    return f'<div class="tile"{style}><div class="label">{_e(label)}</div><div class="value">{_e(value)}</div>' \
           f'<div class="sub">{_e(sub)}</div></div>'


def build_dashboard(app: "App", day: date | None = None) -> Path:
    day = day or app.today()
    store, con = app.store, app.con
    scenarios = load_scenarios(store)
    snapshots = list(store.read("ranking_snapshot"))
    last_date = max((s["date"] for s in snapshots), default=None)
    # 同じ日にレポートを作り直すと、ランキングの記録が日付ごとに重なって残る。最後に作ったものだけを使う(同じ候補は _seq が大きい方)
    newest: dict[tuple, dict] = {}
    for s_ in snapshots:
        if s_["date"] == last_date and not s_.get("empty"):
            key = (s_["scenario_id"], s_["code"])
            if key not in newest or s_.get("_seq", 0) > newest[key].get("_seq", 0):
                newest[key] = s_
    latest = sorted(newest.values(), key=lambda s: s["rank"])
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
    n_pick = sum(1 for s in latest if s["tier"] == "本命")
    n_caution = sum(1 for s in latest if (cands.get((s["scenario_id"], s["code"]), {}).get("side") == "caution"
                                          or (s.get("excluded_reason") or "").startswith("売り方向")))
    kpis = "".join([
        _tile("本命(最新のレポート)", str(n_pick), f"{last_date or 'まだありません'}", "var(--good)"),
        _tile("警戒(下がる見込み)", str(n_caution), "売り方向の候補。本命にはしない", "var(--critical)"),
        _tile("進行中・待機中のシナリオ", str(len(active)), f"上限 {app.th.max_active_scenarios} 件", "var(--c-judge)"),
        _tile(f"今月の的中率(銘柄)", f"{hit / len(month_picks):.0%}" if month_picks else "―",
              f"本命 {len(month_picks)} 件のうち動いた {hit} 件", "var(--gold)"),
        _tile("直近30日の実行", f"{len(executed)} 日", f"作業時間の中央値 {mins[len(mins) // 2]} 分" if mins else "記録なし",
              "var(--c-system)"),
    ])
    names_pick = "、".join(name(s["scenario_id"], s["code"]) for s in latest if s["tier"] == "本命")
    verdict_html = (f'<div class="verdict"><b>本命 {n_pick} 銘柄</b>:{_e(names_pick)}</div>' if n_pick else
                    '<div class="verdict none"><b>本命:該当なし</b>(無理に候補を出さないのが正常な結果)</div>')
    t2, t3, t4 = app.th.star_thresholds
    help_html = (f'<div class="help"><b>自信度の読み方:数字が大きいほど良い(強気)。</b><br>'
                 f'自信度は「この材料で期待できる上昇幅(TOPIXを上回る分)」の期待値です。たとえば <b>3.5%</b> は、'
                 f'平均して +3.5% 上回る見込み、という目安です。<br>'
                 f'計算: 実現確度(そのシナリオどおりに進む確率) × 残り余地(想定上昇幅から、すでに上がった分を引いたもの) × 鮮度(材料が新しいほど大きい)。<br>'
                 f'★: <span class="stars">★</span> {t2:.0%} 未満 / <span class="stars">★★</span> {t2:.0%} 以上 / '
                 f'<span class="stars">★★★</span> {t3:.0%} 以上 / <span class="stars">★★★★</span> {t4:.0%} 以上 '
                 f'(棒の長さは {t4:.0%} を満点にした割合)。<br>'
                 f'<span class="tier caution">警戒</span> は「下がる」と見た候補なので、数字が大きいほど<b>下がる見込みが大きい</b>と読みます。'
                 f'星の基準は暫定で、実績がたまったら決め直します。</div>')

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
            f'<div class="card pick"><h3><span class="tier pick">本命</span> {s["rank"]}位 {_e(name(s["scenario_id"], s["code"]))} '
            f'<span class="stars">{"★" * s["stars"]}</span></h3><dl>'
            f'<dt>シナリオ</dt><dd>{_e(sc.statement)}({_e(sc.scenario_id)}・強さ {_e(sc.strength)})</dd>'
            f'<dt>連想の段数</dt><dd>{_e(c.get("stage"))}段目・{_e(c.get("viewpoint"))}の視点</dd>'
            f'<dt>自信度</dt><dd><b>{s["confidence"]:.1%}</b>(実現確度 {_pct(s["realization_prob"])} × 残り余地 '
            f'{_pct(s["remaining_room"], True)} × 鮮度 {_pct(s["freshness"])})</dd>'
            f'<dt>織り込み度</dt><dd>{_pct(s["priced_in"])}</dd>'
            f'<dt>反応の診断</dt><dd>{_diag(s["diagnosis"])}</dd>'
            f'<dt>裏取り</dt><dd>{verified}/{len(plan)} 確認済み</dd>'
            f'<dt>崩れる条件</dt><dd>{_e(" / ".join(c.get("break_conditions", [])))}</dd>'
            f'<dt>次の材料</dt><dd>{_e(c.get("next_catalyst"))}</dd></dl></div>')
    cards_html = f'<div class="cards">{"".join(cards)}</div>' if cards else \
        '<div class="table-wrap"><div class="empty">本命:該当なし</div></div>'

    # ---- ランキング ----
    rank_rows = []
    for s in latest:
        side = cands.get((s["scenario_id"], s["code"]), {}).get("side")
        caution = side == "caution" or (s.get("excluded_reason") or "").startswith("売り方向")
        rank_rows.append([str(s["rank"]), _e(name(s["scenario_id"], s["code"])),
                          _tier_label(s["tier"], side, s.get("excluded_reason")),
                          _score_cell(s["confidence"], s["stars"], caution, t4),
                          _pct(s["realization_prob"]), _signed(s["remaining_room"]), _pct(s["freshness"]),
                          _pct(s["priced_in"]), _diag(s["diagnosis"]), _e(s.get("excluded_reason") or "")])

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

    # ---- 継続性チェック(選んだ銘柄の期待は、まだ続いているか) ----
    health_level = {"継続": "good", "注意": "warning", "劣化": "critical", "織り込み進行": "serious", "終了": ""}
    health = app.latest_health(day)
    health_rows = [[_e(r.get("company_name") or r["code"]) + f"({_e(r['code'])})", _e(r["scenario_id"]),
                    _badge(health_level.get(r["status"], ""), r["status"]
                           + (f"(前回 {r['prev_status']} から変化)" if r.get("changed") else "")),
                    _signed(r["metrics"].get("excess")), _pct(r["metrics"].get("priced_in")),
                    f"{r['metrics']['volume_ratio']:.0%}" if r["metrics"].get("volume_ratio") is not None else "―",
                    _e(r["metrics"].get("followup_items")), _e(" / ".join(r.get("reasons", [])))] for r in health]

    # ---- 本命の履歴 ----
    hist_rows = []
    for p in sorted(picks, key=lambda p: p["first_pick_date"], reverse=True)[:100]:
        m = moved.get((p["scenario_id"], p["code"]))
        first = date.fromisoformat(p["first_pick_date"])
        window_over = business_days_between(first, day) > min(p["expected_days"], app.th.moved_max_days)
        result = _badge("good", "動いた") if m else (_badge("critical", "動かず") if window_over else _badge("warning", "判定中"))
        hist_rows.append([_e(p["first_pick_date"]), _e(name(p["scenario_id"], p["code"])), _e(p["scenario_id"]),
                          f'{_e(p.get("stage"))}段目', result,
                          _e(m.get("days_to_move")) if m else "―", _signed(m.get("max_excess")) if m else "―"])

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

    news_json, n_news = _news_payload(app)

    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>連想ダッシュボード</title><style>{CSS}</style></head>
<body>
{_site_nav(app, app.dir("reports"))}
<header><h1>連想ダッシュボード</h1>
<span class="meta">作成 {generated} / 最新のレポート {_e(last_date or 'まだありません')}
{f' / <a href="{_e(report_link)}">連想レポートを開く</a>' if report_link else ''}</span></header>
<div class="main-tabs">
<div class="main-tab-btn tab-judge active" id="btn-judge" onclick="showMainTab('judge')">今日の判断<small>要点・本命・ランキング・シナリオ</small></div>
<div class="main-tab-btn tab-check" id="btn-check" onclick="showMainTab('check')">点検と履歴<small>継続性・売りのサイン・本命の履歴</small></div>
<div class="main-tab-btn tab-system" id="btn-system" onclick="showMainTab('system')">システム状態<small>収集・実行記録</small></div>
<div class="main-tab-btn tab-news" id="btn-news" onclick="showMainTab('news')">ニュース一覧({n_news})</div></div>

<div id="tab-judge" class="tabpane tab-judge">
<nav><a href="#summary">要点</a><a href="#picks">本命</a><a href="#ranking">自信度ランキング</a><a href="#scenarios">シナリオ</a></nav>
<main>
<section id="summary"><h2>要点</h2>{verdict_html}<div class="kpis">{kpis}</div>
<p class="note">的中率は改善のための目安。合否の判定は運用開始から約6か月後にまとめて行う(CONCEPT §11)。</p></section>
<section id="picks"><h2>本命</h2>{cards_html}</section>
<section id="ranking"><h2>自信度ランキング({_e(last_date or '―')})</h2>
{help_html}
{_table(["順位", "銘柄", "区分", "自信度", "実現確度", "残り余地", "鮮度", "織り込み", "診断", "本命にしない理由"],
        rank_rows, {0, 4, 5, 6, 7}, "この日の候補はありません" if last_date else "まだランキングがありません")}</section>
<section id="scenarios"><h2>シナリオ</h2>
<p class="note">終了したシナリオは45日分だけ表示する。</p>
{_table(["番号", "シナリオ", "状態", "時間の型", "強さ", "残り(営業日)", "裏取り", "候補"], scen_rows, {5, 6},
        "まだシナリオがありません")}</section>
</main></div>

<div id="tab-check" class="tabpane tab-check" hidden>
<nav><a href="#health">継続性チェック</a><a href="#signals">売りのサイン</a><a href="#history">本命の履歴</a></nav>
<main>
<section id="health"><h2>継続性チェック(選んだ銘柄の期待は、まだ続いているか)</h2>
<p class="note">本命と進行中の候補を、毎日自動で点検した目印(価格・関心・新しい材料・悪材料の語・期間)。判断は最後に人が行う。
状態:🟢継続 / 🟡注意 / 🔴劣化 / 🔵織り込み進行 / ⚪終了。閾値は暫定で、記録を積んで当たりを検証する。</p>
{_table(["銘柄", "シナリオ", "状態", "TOPIX超過", "織り込み度", "売買代金(5日/20日)", "新しい材料", "理由"],
        health_rows, {3, 4, 5, 6}, "対象なし(進行中のシナリオの候補と、本命に挙げた銘柄が、まだ無い)")}</section>
<section id="signals"><h2>売りのサイン(過去に本命とした銘柄)</h2>
{_table(["日付", "銘柄", "シナリオ", "サイン"], sig_rows, empty="売りのサインはありません")}</section>
<section id="history"><h2>本命の履歴</h2>
<p class="note">「動いた」:本命に挙げた日の終値から、想定期間内(最長20営業日)に TOPIX より +10% 以上上回った。</p>
{_table(["本命にした日", "銘柄", "シナリオ", "段数", "結果", "動くまで(営業日)", "最大の超過上昇"], hist_rows, {5, 6},
        "まだ本命がありません")}</section>
</main></div>

<div id="tab-system" class="tabpane tab-system" hidden>
<nav><a href="#collect">収集の状態</a><a href="#runs">実行記録</a></nav>
<main>
<section id="collect"><h2>収集の状態</h2>
<p class="note">ニュース {_e(counts['news_item'])} 件 / 開示 {_e(counts['disclosure'])} 件 / 上場銘柄一覧 {_e(counts['universe_daily'])} 行</p>
{_table(["収集器", "状態", "最後に成功", "最後に実行", "件数(直近)", "メッセージ"], fetch_rows, {4},
        "まだ収集していません(python -m assoc collect)")}</section>
<section id="runs"><h2>実行記録(夜の手順)</h2>
{_table(["日付", "実行", "手順", "所要(分)", "見送った理由"], run_rows, {3}, "まだ記録がありません")}</section>
</main></div>

<div id="tab-news" class="tabpane tab-news" hidden>
<section><h2>ニュース一覧(直近{NEWS_DAYS}日に取得したもの)</h2>
<p class="note">連想モデルが自動で集めたニュースと開示の全件。見出しをクリックすると元の記事を開く(Google News は中継URLのため開けないことがある)。</p>
<div class="newsbar"><input type="text" id="nf-q" placeholder="キーワード(スペースで AND) 例: TOB カカクコム">
<select id="nf-kind"><option value="">種類: すべて</option><option value="0">ニュース</option><option value="1">開示(TDnet・EDINET)</option></select>
<select id="nf-media"></select><select id="nf-day"></select><button id="nf-reset" type="button">リセット</button>
<span class="note" id="nf-count"></span></div>
<div class="table-wrap"><table id="news-table"><thead><tr><th>取得 / 公表</th><th>種類</th><th>媒体</th><th>見出し</th><th>銘柄</th></tr></thead>
<tbody id="nt-body"></tbody></table></div>
<div class="newsbar" style="margin-top:10px"><button id="nf-prev" type="button">前の200件</button>
<span class="note" id="nf-pos"></span><button id="nf-next" type="button">次の200件</button></div></section>
</div>
<script>window.NEWS_DATA = {news_json};</script>
<script>{NEWS_JS}</script>
</body></html>
"""
    path = app.dir("reports") / "dashboard.html"
    path.write_text(html, encoding="utf-8")
    _mirror_to_existing(app, html, path.parent, report_link)
    return path


MIRROR_NAME = "モデル⑧ニュース連想.html"


def _mirror_to_existing(app: "App", html: str, out_dir: Path, report_link: str | None) -> None:
    """既存システムの dashboard フォルダにも同じページを置く(そこから開けるように)。

    書くのはこの1ファイルだけ。上部ナビと、レポートへのリンクは、置き先からの相対パスに直す。
    置き先が無い・書けないときは、何もしない(本体の dashboard.html は作れている)。
    """
    import os
    from urllib.parse import quote

    directory = app.cfg.raw.get("paths", {}).get("existing_dashboards") or ""
    if not directory or not Path(directory).is_dir():
        return
    target_dir = Path(directory)
    try:
        nav_old = _site_nav(app, out_dir)
        nav_new = _site_nav(app, target_dir, in_folder=True)
        out = html.replace(nav_old, nav_new)
        if report_link:
            rel = quote(Path(os.path.relpath(out_dir / report_link, target_dir)).as_posix(), safe="/")
            out = out.replace(f'href="{_e(report_link)}"', f'href="{rel}"')
        (target_dir / MIRROR_NAME).write_text(out, encoding="utf-8")
    except OSError:
        pass
