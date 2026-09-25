"""夕方の処理:イベント化・足切り・入力パックの作成(docs/DESIGN.md §6.2・§6.3)。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from assoc.config import REPO_ROOT
from assoc.events.cluster import build_events_between, extract_entities
from assoc.events.gate import GateInput, gate_events
from assoc.state import latest_by, load_scenarios
from assoc.timeutil import JST, parse_iso, to_iso, utcnow

if TYPE_CHECKING:
    from assoc.app import App

PACK_VERSION = 1


def _attention_history(app: "App", day: date) -> dict[str, list[float]]:
    since = (day - timedelta(days=app.th.gate_lookback_days)).isoformat()
    hist: dict[str, list[float]] = {"media_count": [], "gdelt": [], "wiki": [], "growth": []}
    for r in app.store.read("event_gate"):
        if since <= r.get("date", "") < day.isoformat():
            a = r.get("attention", {})
            for k in hist:
                if a.get(k) is not None:
                    hist[k].append(float(a[k]))
    return hist


def _lookup_attention(con, key: str, source: str, day: date) -> float:
    row = con.execute("SELECT value FROM attention_daily WHERE key = ? AND source = ? AND date <= ? "
                      "ORDER BY date DESC LIMIT 1", [key, source, day]).fetchone()
    return float(row[0]) if row else 0.0


def _load_themes() -> dict[str, str]:
    """THEMES.md の表から テーマID → 名前 を読む(ID は P01 / F01 / M01 / G01 / E01 の形式)。"""
    import re
    text = (REPO_ROOT / "docs" / "THEMES.md").read_text(encoding="utf-8")
    return {m.group(1): m.group(2).strip().strip("*")
            for m in re.finditer(r"^\| ([PFMGE]\d{2}) \| ([^|]+)\|", text, re.M)}


def _load_past_cases() -> list[dict[str, Any]]:
    cases = []
    for f in sorted((REPO_ROOT / "knowledge" / "past_cases").glob("PC*.yaml")):
        with f.open(encoding="utf-8") as fh:
            cases.append(yaml.safe_load(fh))
    return cases


def _previous_pack_time(packs: Path, day: date) -> datetime | None:
    earlier = sorted(p for p in packs.glob("pack_*.json") if p.stem[5:] < day.isoformat())
    if not earlier:
        return None
    try:
        return parse_iso(json.loads(earlier[-1].read_text(encoding="utf-8"))["generated_at"])
    except (KeyError, ValueError):
        return None


def build_pack(app: "App", day: date) -> Path:
    packs = app.dir("packs")
    path = packs / f"pack_{day.isoformat()}.json"
    if path.exists():
        return path

    scenarios = load_scenarios(app.store)
    active = [s for s in scenarios.values() if s.is_active]
    active_codes = {c["code"] for s in active for c in s.candidates}
    # 別枠の判定に使う語:候補の社名と、シナリオの文の固有表現
    active_keywords = {c.get("master_name") or c["company_name"] for s in active for c in s.candidates}
    active_keywords |= {w for s in active for w in extract_entities(s.statement) if len(w) >= 2}

    # 前回のパックを作った時刻から今回までに取得したものを対象にする(夜に取得したニュースも漏らさない)
    now = utcnow()
    day_start = datetime.combine(day, time.min, tzinfo=JST)
    window_end = min(now, day_start + timedelta(days=1)) if day <= app.today() else day_start + timedelta(days=1)
    window_start = _previous_pack_time(packs, day) or day_start
    events = build_events_between(app.con, window_start, window_end)
    seen = {r["novelty_hash"] for r in app.store.read("event_gate") if r.get("novelty_hash") and r.get("date") != day.isoformat()}
    hist = _attention_history(app, day)
    inputs = [GateInput(event_id=e.event_id, title=e.title, novelty_hash=e.novelty_hash,
                        media_count=float(e.media_count), disclosure_type=e.disclosure_type,
                        codes=list(e.codes), entities=list(e.entities)) for e in events]
    results = gate_events(inputs, app.th, media_history=hist["media_count"], gdelt_history=hist["gdelt"],
                          wiki_history=hist["wiki"], growth_history=hist["growth"], seen_novelty_hashes=seen,
                          active_codes=active_codes, active_keywords=active_keywords)

    by_id = {e.event_id: e for e in events}
    media_sorted = sorted(hist["media_count"])
    high_cut = media_sorted[int(len(media_sorted) * app.th.attention_high_pct)] if len(media_sorted) >= 20 else None
    pack_events = []
    for r in results:
        ev = by_id[r.event_id]
        # 比べる履歴が足りない間は判定しない(None)。評価の側で等級から補う
        attention_high = None if high_cut is None else ev.media_count > high_cut
        app.store.append("event_gate", {"event_id": r.event_id, "date": day.isoformat(), "passed": r.passed,
                                        "reason": r.reason, "forced_in": r.forced_in, "attention": r.attention,
                                        "attention_high": attention_high, "novelty_hash": ev.novelty_hash,
                                        "title": ev.title})
        if r.passed:
            urls = [row[0] for row in app.con.execute(
                "SELECT url FROM news_item WHERE news_id IN (SELECT unnest(?)) "
                "UNION ALL SELECT url FROM disclosure WHERE disclosure_id IN (SELECT unnest(?))",
                [ev.news_ids, ev.disclosure_ids]).fetchall()]
            pack_events.append({"event_id": ev.event_id, "title": ev.title, "urls": urls[:10],
                                "first_observed_at": to_iso(ev.first_observed_at), "media_count": ev.media_count,
                                "disclosure_type": ev.disclosure_type, "codes": ev.codes,
                                "gate_reason": r.reason, "forced_in": r.forced_in,
                                "attention_high": attention_high})

    themes = _load_themes()
    cards = latest_by(app.store, "premise_card", "theme_id")
    temps = latest_by(app.store, "theme_temperature", "theme_id")
    last_temp = max((r.get("run_date", "") for r in temps.values()), default="")
    temperature_due = not last_temp or (day - date.fromisoformat(last_temp)).days >= 7
    theme_rows = [{"theme_id": tid, "name": name, "temperature": temps.get(tid, {}).get("temperature"),
                   "premise_card": {k: cards[tid][k] for k in ("version", "market_premise", "key_numbers",
                                                               "breaking_directions")} if tid in cards else None}
                  for tid, name in themes.items()]

    releases_today = [{"scenario_id": s.scenario_id, "arrow_no": no, "indicator": p.get("indicator"),
                       "source": p.get("source")}
                      for s in active for no, p in s.check_plan.items() if p.get("next_release") == day.isoformat()]

    codes = sorted(active_codes | {c for e in pack_events for c in e["codes"]})
    market = _market_state(app, codes, day)

    pack = {
        "pack_version": PACK_VERSION, "date": day.isoformat(), "generated_at": to_iso(now), "mode": "通常",
        "window": {"from": to_iso(window_start), "to": to_iso(window_end)},
        "limits": {"max_candidates_per_scenario": app.th.max_candidates_per_scenario,
                   "max_active_scenarios": app.th.max_active_scenarios},
        "events": pack_events,
        "themes": theme_rows,
        "active_scenarios": [{
            "scenario_id": s.scenario_id, "statement": s.statement, "status": s.status, "time_type": s.time_type,
            "phase": s.phase, "start_condition": s.start_condition, "end_condition": s.end_condition,
            "strength": s.strength, "expected_rise": s.expected_rise, "expected_days": s.expected_days,
            "started_date": s.started_date.isoformat() if s.started_date else None, "theme_ids": s.theme_ids,
            "check_plan": [{"arrow_no": no, "arrow": p.get("arrow"), "status": p.get("status"),
                            "next_release": p.get("next_release")} for no, p in sorted(s.check_plan.items())],
            "candidates": [{"code": c["code"], "company_name": c.get("master_name") or c["company_name"],
                            "stage": c.get("stage")} for c in s.candidates]} for s in active],
        "past_cases": _load_past_cases(),
        "market": market,
        "releases_today": releases_today,
        # 週1回、テーマの温度を更新する日かどうか(前回の更新から7日以上たっていれば true)
        "temperature_update_due": temperature_due,
        "temperature_last_updated": last_temp or None,
    }
    body = json.dumps(pack, ensure_ascii=False, sort_keys=True, default=str)
    pack["input_pack_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def _market_state(app: "App", codes: list[str], day: date) -> dict[str, Any]:
    from assoc.market import indicators as ind
    from assoc.pipeline.evaluate import PriceLoader
    from assoc.market.prices import price_source_from_config
    out: dict[str, Any] = {}
    try:
        loader = PriceLoader(price_source_from_config(app.cfg), day)
    except ValueError:
        return out
    topix = loader.get(app.topix_code())
    for code in codes:
        df = loader.get(code)
        if df.empty or topix.empty:
            continue
        last = df["date"].iloc[-1]
        try:
            out[code] = {"date": last.isoformat(), "close": float(df["close"].iloc[-1]),
                         "day_excess_sigma": round(ind.day_excess_sigma(df, topix, last, app.th.vol_window), 2),
                         "return_20d": round(ind.return_over(df, last, 20), 4),
                         "avg_turnover_yen": round(ind.avg_turnover(df, last, app.th.vol_window))}
        except (ValueError, KeyError, IndexError, ZeroDivisionError):
            continue
    return out
