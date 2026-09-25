"""月次の振り返り(docs/CONCEPT.md §9.2、docs/DESIGN.md §6.9)。

Python は数字を集計し、見逃しの候補(S・A級のイベントのあとに「動いた」が候補に挙げていない銘柄)を
抽出する。関係の判定・原因の分析・改善の提案は LLM(/association-monthly)が行う。
合否(L2)の判定はしない。
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema

from assoc.config import REPO_ROOT
from assoc.state import load_scenarios

if TYPE_CHECKING:
    from assoc.app import App


def previous_month(today: date) -> str:
    first = today.replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _in_month(iso: str | None, month: str) -> bool:
    return bool(iso) and iso[:7] == month


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(centre - half, 3), round(centre + half, 3)


def metrics(app: "App", month: str) -> dict[str, Any]:
    picks = [p for p in app.store.read("pick_tracking") if p.get("event") == "first_pick"
             and _in_month(p.get("first_pick_date"), month)]
    moved = {(p["scenario_id"], p["code"]): p for p in app.store.read("pick_tracking") if p.get("event") == "moved"}

    n = len(picks)
    k = sum(1 for p in picks if (p["scenario_id"], p["code"]) in moved)
    scen = sorted({p["scenario_id"] for p in picks})
    scen_hit = sum(1 for s in scen if any((s, p["code"]) in moved for p in picks if p["scenario_id"] == s))
    stage1 = [p for p in picks if p.get("stage") == 1]
    stage1_hit = sum(1 for p in stage1 if (p["scenario_id"], p["code"]) in moved)
    leads = [moved[(p["scenario_id"], p["code"])].get("days_to_move") for p in picks
             if (p["scenario_id"], p["code"]) in moved]
    lead_ok = sum(1 for d in leads if d is not None and d >= 1)

    runs = [r for r in app.store.read("run_log") if _in_month(r.get("date"), month)]
    executed = [r for r in runs if r.get("executed")]
    durations = sorted(r["duration_min"] for r in executed if r.get("duration_min") is not None)
    return {
        "month": month,
        "picks": n,
        "hit_rate_by_stock": round(k / n, 3) if n else None,
        "scenarios_with_picks": len(scen),
        "hit_rate_by_scenario": round(scen_hit / len(scen), 3) if scen else None,
        "hit_rate_by_scenario_ci95": _wilson(scen_hit, len(scen)),
        "stage1_only_hit_rate": round(stage1_hit / len(stage1), 3) if stage1 else None,
        "lead_rate": round(lead_ok / len(leads), 3) if leads else None,
        "base_rate": base_rate(app, month),
        "operation": {"executed_days": len(executed), "skipped_days": len(runs) - len(executed),
                      "median_minutes": durations[len(durations) // 2] if durations else None,
                      "skip_reasons": [r.get("skip_reason") for r in runs if not r.get("executed")]},
        "note": "月次のレポートでは合否を判定しない(CONCEPT §9.2)",
    }


def base_rate(app: "App", month: str) -> float | None:
    """偶然の的中率:その月の各営業日を起点に、全銘柄のうち20営業日以内に「動いた」銘柄の割合の近似。

    price_copy にある銘柄(候補・検算に使った銘柄)で計算する。全銘柄の株価DBを読めるようになったら
    (S1 の後)、そちらに切り替える。"""
    rows = app.con.execute(
        "SELECT code, date, close FROM price_copy WHERE strftime(date, '%Y-%m') = ? ORDER BY code, date",
        [month]).fetchall()
    if not rows:
        return None
    topix_code = app.topix_code()
    series: dict[str, list[tuple[date, float]]] = {}
    for code, d, close in rows:
        series.setdefault(code, []).append((d, close))
    topix = dict(series.pop(topix_code, []))
    if not topix:
        return None
    hits = total = 0
    for code, s in series.items():
        if len(s) < 2:
            continue
        base_d, base_c = s[0]
        if base_d not in topix:
            continue
        total += 1
        best = max((c / base_c - 1) - (topix.get(d, topix[base_d]) / topix[base_d] - 1) for d, c in s[1:21])
        hits += best >= app.th.moved_excess
    return round(hits / total, 3) if total else None


def prepare(app: "App", month: str) -> Path:
    scenarios = load_scenarios(app.store)
    grades = {g["event_id"]: g for g in app.store.read("grade")}
    finals = {f["event_id"]: f["final_grade"] for f in app.store.read("grade_final")}
    sa_events = [eid for eid, g in grades.items() if _in_month(g.get("run_date"), month)
                 and finals.get(eid, g["provisional_grade"]) in ("S", "A")]
    candidates_by_event: dict[str, set[str]] = {}
    for sc in scenarios.values():
        candidates_by_event.setdefault(sc.event_id, set()).update(c["code"] for c in sc.candidates)

    moved = {(p["scenario_id"], p["code"]) for p in app.store.read("pick_tracking") if p.get("event") == "moved"}
    failures = []
    for p in app.store.read("pick_tracking"):
        if p.get("event") != "first_pick" or not _in_month(p.get("first_pick_date"), month):
            continue
        if (p["scenario_id"], p["code"]) in moved:
            continue
        sc = scenarios.get(p["scenario_id"])
        failures.append({"scenario_id": p["scenario_id"], "code": p["code"],
                         "statement": sc.statement if sc else None,
                         "check_plan": [{"arrow_no": no, "arrow": c.get("arrow"), "status": c.get("status")}
                                        for no, c in sorted(sc.check_plan.items())] if sc else []})

    dropped = [r for r in app.store.read("event_gate") if _in_month(r.get("date"), month) and not r.get("passed")]
    review_input = {
        "month": month,
        "metrics": metrics(app, month),
        "sa_events": [{"event_id": e, "grade": finals.get(e, grades[e]["provisional_grade"]),
                       "candidates": sorted(candidates_by_event.get(e, set()))} for e in sa_events],
        "movers": [],   # 全銘柄の株価DBに接続したら(S1 の後)、イベント後20営業日に「動いた」銘柄をここに入れる
        "failures": failures,
        "gate_dropped": [{"event_id": r["event_id"], "title": r.get("title"), "reason": r.get("reason"),
                          "media_count": r.get("attention", {}).get("media_count")} for r in dropped][:50],
    }
    path = app.dir("reviews") / f"review_input_{month}.json"
    path.write_text(json.dumps(review_input, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def commit(app: "App", month: str) -> dict[str, Any]:
    path = app.dir("inbox") / f"monthly_{month}.json"
    if not path.exists():
        return {"ok": False, "errors": [f"出力がありません: {path}"]}
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    schema = json.loads((REPO_ROOT / "schemas" / "monthly_output.schema.json").read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]
    if errors:
        return {"ok": False, "errors": errors}
    review_input_path = app.dir("reviews") / f"review_input_{month}.json"
    review_input = json.loads(review_input_path.read_text(encoding="utf-8")) if review_input_path.exists() else {}
    m = review_input.get("metrics") or metrics(app, month)
    related = [r for r in doc["related_movers"] if r["related"]]
    m["capture_note"] = f"事後の判定で関係ありとした見逃し {len(related)} 件"
    app.store.append("monthly_review", {"month": month, "metrics": m, **doc})
    report = app.dir("reports") / f"monthly_{month}.md"
    report.write_text(render(month, m, doc), encoding="utf-8")
    return {"ok": True, "report": report}


def render(month: str, m: dict[str, Any], doc: dict[str, Any]) -> str:
    def pct(x):
        return "―" if x is None else f"{x:.0%}"
    op = m.get("operation", {})
    lines = [f"# 振り返りレポート {month}", "", "> 月次のレポートでは合否を判定しない。目的は改善点を見つけること。", "",
             "## 1. 見逃し", ""]
    lines += [f"- {x['code']}({x['event_id']}):{x['cause']} ― {x['detail']}" for x in doc["miss_analysis"]] or ["- なし"]
    lines += ["", "## 2. 外れ", ""]
    lines += [f"- {x['code']}({x['scenario_id']}):矢印{x['wrong_arrow'] or '―'} ― {x['detail']}"
              for x in doc["failure_analysis"]] or ["- なし"]
    lines += ["", "## 3. 指標", "",
              "| 指標 | 値 |", "|---|---|",
              f"| 本命の数 | {m.get('picks')} |",
              f"| 的中率(銘柄) | {pct(m.get('hit_rate_by_stock'))} |",
              f"| 的中率(シナリオ) | {pct(m.get('hit_rate_by_scenario'))}(95%区間 {m.get('hit_rate_by_scenario_ci95')}) |",
              f"| 偶然の的中率 | {pct(m.get('base_rate'))} |",
              f"| 1段目だけの的中率 | {pct(m.get('stage1_only_hit_rate'))} |",
              f"| 先行性 | {pct(m.get('lead_rate'))} |",
              f"| 捕捉率 | {m.get('capture_note', '―')} |",
              "", "## 4. 改善の提案(ユーザーの了承後に反映する)", ""]
    lines += [f"- [{x['target']}] {x['proposal']}(理由:{x['reason']})" for x in doc["improvements"]] or ["- なし"]
    lines += ["", "## 5. 運用の状況", "",
              f"- 実行した日 {op.get('executed_days')} 日 / 実行しなかった日 {op.get('skipped_days')} 日"
              f" / 作業時間の中央値 {op.get('median_minutes')} 分", ""]
    return "\n".join(lines)
