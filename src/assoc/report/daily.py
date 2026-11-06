"""O1 連想レポートと O3 連携ファイル(docs/CONCEPT.md §9.1・§9.3)。"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from assoc.pipeline.evaluate import CandidateEval
from assoc.state import ScenarioState

STAR = "★"


@dataclass
class DayContext:
    """レポートに載せる、その日の出来事。"""

    new_scenarios: list[ScenarioState] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)        # scenario_update の行
    ended: list[tuple[ScenarioState, str]] = field(default_factory=list)
    grades: dict[str, dict[str, Any]] = field(default_factory=dict)    # event_id -> grade(確定があれば確定)
    evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # scenario_id -> 証拠
    sell_signals: list[tuple[str, str, list[str]]] = field(default_factory=list)  # (code, 社名, サイン)
    warnings: list[str] = field(default_factory=list)
    health: list[dict[str, Any]] = field(default_factory=list)         # 最新日の継続性チェック(重い順)
    exit_calls: list[dict[str, Any]] = field(default_factory=list)     # その日の夜の判断が出した撤退・縮小(D51)
    exit_watch: dict[str, Any] = field(default_factory=dict)           # 機械の目印(株価の絶対の動き。D51)


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "―"
    return f"{x:+.0%}" if signed else f"{x:.0%}"


def _card(e: CandidateEval, ctx: DayContext, n: int) -> list[str]:
    sc, c = e.scenario, e.candidate
    name = c.get("master_name") or c["company_name"]
    er = sc.expected_rise
    grade = ctx.grades.get(sc.event_id, {})
    grade_txt = grade.get("final_grade") and f"{grade['final_grade']}(確定)" or \
        (grade.get("provisional_grade") and f"{grade['provisional_grade']}(暫定。翌日に確定)") or "―"
    lines = [f"### 本命{n}位 {name}({c['code']})  自信度 {STAR * e.stars}", "",
             f"- **シナリオ**:{sc.statement}({sc.scenario_id}・強さ {sc.strength})",
             f"- **等級**:{grade_txt}" + ("(新テーマ候補:どのテーマにも当てはまらない)" if sc.new_theme_flag else ""),
             f"- **連想の道筋**:{c.get('stage')}段目・{c.get('viewpoint')}の視点 ― {c.get('rationale', '')}",
             f"- **材料の大きさ**:想定上昇幅 {_pct(er.get('low'), True)}〜{_pct(er.get('high'), True)}"
             f"(中央 {_pct(er.get('median'), True)})。根拠:{er.get('basis', '')}"]
    if sc.status == "待機":
        lines.append(f"- **鮮度**:待機中(起動条件:{sc.start_condition or '―'})")
    else:
        lines.append(f"- **鮮度**:{_pct(e.freshness)}({sc.time_type}型・想定期間 {sc.expected_days} 営業日・"
                     f"残り {e.remaining_days if e.remaining_days is not None else '―'} 営業日)")
    lines += [f"- **織り込み度**:{_pct(e.priced_in)}(超過上昇 {_pct(e.excess, True)} ÷ 想定上昇幅 {_pct(er.get('median'), True)})",
              f"- **反応の診断**:{e.diagnosis}",
              f"- **実現確度**:{_pct(e.realization_prob)}({' / '.join(e.prob_factors)})"]
    plan = sc.check_plan
    if plan:
        lines.append("- **裏取り**:")
        mark = {"確認": "✓", "否定": "✗"}
        for no in sorted(plan):
            p = plan[no]
            status = p.get("status", "未確認")
            lines.append(f"  - 矢印{no} {p.get('arrow', '')} ― {mark.get(status, '')} {status}"
                         + (f"(次の確認:{p['next_release']})" if status == "未確認" and p.get("next_release") else ""))
    for ev in ctx.evidence.get(sc.scenario_id, []):
        if ev.get("supports") == "against":
            lines.append(f"- **反証**:{ev.get('value', '')}({ev.get('url', '')}・信頼度{ev.get('source_tier')})")
    breaks = list(c.get("break_conditions", []))
    if e.aids and e.aids.earnings_warning:
        breaks.append("(自動)想定期間内に決算発表がある")     # DESIGN §8.2
    lines += [f"- **崩れる条件**:{' / '.join(breaks)}",
              f"- **次の材料**:{c.get('next_catalyst', '―')}"]
    if e.aids:
        a = e.aids
        earn = "―" if a.days_to_earnings is None else f"{'⚠ ' if a.earnings_warning else ''}{a.days_to_earnings} 営業日"
        lines.append(f"- **売買の目安**:決算発表まで {earn} / 権利落ち "
                     f"{'あり(期間内)' if a.ex_rights_within_period else 'なし'} / 信用規制 "
                     f"{'あり' if a.credit_restriction else 'なし'} / 普段の1日の値動き ±{a.daily_vol:.1%} / "
                     f"値幅制限 ±{a.price_limit_yen:,.0f}円")
    if e.notes:
        lines.append(f"- 注記:{' / '.join(e.notes)}")
    lines += [f"- あなたの判断(任意):`python -m assoc decide {c['code']} 買う --reason \"一言\"`(買う/監視/見送り)", ""]
    return lines


_ACTION_MARK = {"撤退": "🔴 撤退", "縮小": "🟠 縮小", "警戒": "🟡 警戒", "継続": "🟢 継続"}
_LEVEL_MARK = {"撤退検討": "🔴 撤退検討", "縮小検討": "🟠 縮小検討", "警戒": "🟡 警戒"}


def _fmt_codes(codes: list[dict[str, Any]]) -> str:
    return "、".join(f"{c.get('company_name') or c.get('name') or c['code']}({c['code']})" for c in codes)


def render_exit_section(calls: list[dict[str, Any]], watch: dict[str, Any]) -> list[str]:
    """撤退・縮小の提案(D51)。夜の判断(理由つき)を先に、機械の目印を後に。売買の指示ではなく判断材料。"""
    lines = ["## 撤退・縮小の提案", "",
             "> 売買の指示ではなく判断材料です。空売りの推奨は含みません。戻ってよい条件と、判断が外れる条件も書いています。", ""]
    order = {"撤退": 0, "縮小": 1, "警戒": 2, "継続": 3}
    for c in sorted(calls, key=lambda c: order.get(c["action"], 9)):
        urg = f"({c['urgency']})" if c.get("urgency") else ""
        lines += [f"### {_ACTION_MARK.get(c['action'], c['action'])}{urg}:{c['target']}", "",
                  f"- **理由**:{c['reason']}", f"- **きっかけ**:{c['trigger']}"]
        if c.get("codes"):
            lines.append(f"- **対象の銘柄**:{_fmt_codes(c['codes'])}")
        lines += [f"- **戻ってよい条件**:{c['reentry_conditions']}", f"- **この判断が外れる条件**:{c['wrong_if']}"]
        for u in c.get("evidence_urls", [])[:3]:
            lines.append(f"- 根拠:{u}")
        lines.append("")
    stocks = watch.get("stocks") or []
    themes = watch.get("themes") or []
    if watch.get("error"):
        lines += [f"- ⚠ {watch['error']}", ""]
    if stocks or themes:
        lines += [f"### 機械の目印(株価の絶対の動き。シナリオが終わった銘柄も含む。追跡 {watch.get('tracked_codes', 0)} 銘柄)", ""]
        if themes:
            lines += ["| テーマ | 目印 | 5日 | 1か月 | 理由 | 追っている銘柄 |", "|---|---|---|---|---|---|"]
            for t in themes:
                aff = "、".join(f"{a['name']}({a['code']}・{'/'.join(a['sources'])})" for a in t["affected_holdings"]) or "―"
                lines.append(f"| {t['name']} | {_LEVEL_MARK.get(t['level'], t['level'])} | {_pct(t.get('median_5d'), True)} | "
                             f"{_pct(t.get('median_1m'), True)} | {' / '.join(t['reasons'])} | {aff} |")
            lines.append("")
        if stocks:
            lines += ["| 銘柄 | 目印 | 本命にした日から | 高値から | 5日 | 理由 |", "|---|---|---|---|---|---|"]
            for s in stocks:
                m = s["metrics"]
                lines.append(f"| {s['name']}({s['code']}・{'/'.join(s['sources'])}) | {_LEVEL_MARK.get(s['level'], s['level'])} | "
                             f"{_pct(m.get('since_pick'), True)} | {_pct(m.get('from_peak'), True)} | "
                             f"{_pct(m.get('ret_5d'), True)} | {' / '.join(s['reasons'])} |")
            lines.append("")
        if not calls and (stocks or themes):
            lines += ["⚠ 夜の判断はこの目印を評価していません(理由つきの提案なし)。目印だけを参考にしてください。", ""]
    if not calls and not stocks and not themes and not watch.get("error"):
        lines += ["- 目印なし(追跡中の銘柄・テーマに、大きな下落はありません)", ""]
    return lines


def render_daily(eval_date: date, evals: list[CandidateEval], ctx: DayContext) -> str:
    picks = [e for e in evals if e.tier == "本命"]
    lines = [f"# 連想レポート {eval_date.isoformat()}", "",
             f"**今日の結論**:本命 {len(picks)} 銘柄 / 新しいシナリオ {len(ctx.new_scenarios)} 件 / "
             f"強化 {sum(1 for u in ctx.updates if u['type'] == '強化')} 件 / 終了 {len(ctx.ended)} 件 / "
             f"撤退・縮小 {sum(1 for c in ctx.exit_calls if c['action'] in ('撤退', '縮小'))} 件", ""]
    if ctx.warnings:
        lines += ["> **注意**", *[f"> - {w}" for w in ctx.warnings], ""]
    lines += render_exit_section(ctx.exit_calls, ctx.exit_watch)
    if not picks:
        lines += ["**本命:該当なし**", ""]

    lines += ["## 自信度ランキング(進行中の全シナリオから)", "",
              "| 順位 | 銘柄 | 区分 | 自信度 | 実現確度 | 残り余地 | 鮮度 | 織り込み | 診断 | 本命にしない理由 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for e in evals:
        c = e.candidate
        side = "警戒" if c.get("side") == "caution" else e.tier
        lines.append(f"| {e.rank} | {c.get('master_name') or c['company_name']}({c['code']}) | {side} | "
                     f"{STAR * e.stars} {e.confidence:.3f} | {_pct(e.realization_prob)} | {_pct(e.remaining_room, True)} | "
                     f"{_pct(e.freshness)} | {_pct(e.priced_in)} | {e.diagnosis} | {e.excluded_reason or ''} |")
    lines.append("")

    for n, e in enumerate(picks, 1):
        lines += _card(e, ctx, n)

    lines += ["## シナリオの変化", ""]
    if not (ctx.new_scenarios or ctx.updates or ctx.ended):
        lines.append("- なし")
    for sc in ctx.new_scenarios:
        lines.append(f"- 新規 {sc.scenario_id}「{sc.statement}」({sc.status}・{sc.time_type}型)")
    for u in ctx.updates:
        lines.append(f"- {u['scenario_id']} {u['type']}:{u.get('new_fact_summary', '')}(強さ → {u.get('strength_after')})")
    for sc, reason in ctx.ended:
        lines.append(f"- {sc.scenario_id} 終了:{reason}")
    lines.append("")

    lines += ["## 過去に本命とした銘柄の追跡(売りのサイン)", ""]
    if not ctx.sell_signals:
        lines.append("- なし")
    for code, name, signals in ctx.sell_signals:
        lines.append(f"- {name}({code}) ⚠ {' / '.join(signals)}")
    lines.append("")

    lines += ["## 継続性チェック(選んだ銘柄の期待は、まだ続いているか)", ""]
    if not ctx.health:
        lines.append("- 対象なし(進行中のシナリオの候補と、本命に挙げた銘柄が、まだ無い)")
    else:
        lines += [f"基準日 {ctx.health[0].get('date')}。自動で数えた目印です(判断は最後にあなたが行います)。", "",
                  "| 銘柄 | シナリオ | 状態 | 理由 |", "|---|---|---|---|"]
        for r in ctx.health:
            mark = {"継続": "🟢", "注意": "🟡", "劣化": "🔴", "織り込み進行": "🔵", "終了": "⚪"}.get(r["status"], "")
            chg = f"(前回 {r['prev_status']} から変化)" if r.get("changed") else ""
            lines.append(f"| {r.get('company_name') or r['code']}({r['code']}) | {r['scenario_id']} | "
                         f"{mark} {r['status']}{chg} | {' / '.join(r.get('reasons', []))} |")
    lines.append("")
    return "\n".join(lines)


def write_daily(report_dir: Path, export_dir: Path, eval_date: date, evals: list[CandidateEval],
                ctx: DayContext) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    md = report_dir / f"report_{eval_date.isoformat()}.md"
    md.write_text(render_daily(eval_date, evals, ctx), encoding="utf-8")
    csv_path = export_dir / f"picks_{eval_date.strftime('%Y%m%d')}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:      # Excel で開けるよう BOM 付き
        w = csv.writer(f)
        w.writerow(["日付", "銘柄コード", "社名", "シナリオ番号", "自信度", "崩れる条件"])
        for e in evals:
            if e.tier == "本命":
                c = e.candidate
                w.writerow([eval_date.isoformat(), c["code"], c.get("master_name") or c["company_name"],
                            e.scenario.scenario_id, f"{e.confidence:.4f}", " / ".join(c.get("break_conditions", []))])
    return md, csv_path
