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


def render_daily(eval_date: date, evals: list[CandidateEval], ctx: DayContext) -> str:
    picks = [e for e in evals if e.tier == "本命"]
    lines = [f"# 連想レポート {eval_date.isoformat()}", "",
             f"**今日の結論**:本命 {len(picks)} 銘柄 / 新しいシナリオ {len(ctx.new_scenarios)} 件 / "
             f"強化 {sum(1 for u in ctx.updates if u['type'] == '強化')} 件 / 終了 {len(ctx.ended)} 件", ""]
    if ctx.warnings:
        lines += ["> **注意**", *[f"> - {w}" for w in ctx.warnings], ""]
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
