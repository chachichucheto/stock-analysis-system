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
from assoc.events.burst import ThemeBurst, detect_bursts
from assoc.events.macro_drivers import detect_macro_drivers, theme_rotation
from assoc.events.cluster import build_events_between, extract_entities, media_of
from assoc.events.terms import detect_emerging_terms
from assoc.events.gate import GateInput, gate_events
from assoc.pack.auto_temperature import auto_signal, should_write
from assoc.state import latest_by, load_scenarios
from assoc.timeutil import JST, parse_iso, to_iso, utcnow

if TYPE_CHECKING:
    from assoc.app import App

PACK_VERSION = 1
MAX_KEYWORD_HITS = 8   # シナリオの文から取った語が、このウィンドウでこの件数を超えて当たる語は、別枠(forced_in)の判定に使わない


def _attention_history(app: "App", day: date) -> dict[str, list[float]]:
    since = (day - timedelta(days=app.th.gate_lookback_days)).isoformat()
    hist: dict[str, list[float]] = {"media_count": [], "gdelt": [], "wiki": [], "growth": []}
    for r in app.store.read("event_gate"):
        if r.get("event_id", "").startswith(("burst-", "shock-", "struct-")):   # 件数急増・株価急変・構造変化の記録は、1件ごとの注目度の分布に混ぜない(D37・D40)
            continue
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


def _judged_dates(app: "App") -> set[str]:
    """夜の手順(判定)まで終わった日。inbox/processed の daily_YYYY-MM-DD.json があるものを数える。"""
    d = app.dir("inbox") / "processed"
    return {f.stem[len("daily_"):] for f in d.glob("daily_*.json")} if d.exists() else set()


def _previous_pack_time(packs: Path, day: date, judged: set[str] | None = None) -> datetime | None:
    """前回のパックの時刻。judged を渡すと、判定まで終わった日のパックだけを前回とみなす(D42)。

    夜の手順が動かなかった日(PC停止・トークン切れ)にも prepare はパックだけ作る。それを前回とみなすと、
    判定されないまま通り過ぎたイベントが、次の判定から漏れる(2026-10-12・10-14 のパックで起きた)。"""
    earlier = sorted(p for p in packs.glob("pack_*.json") if p.stem[5:] < day.isoformat()
                     and (judged is None or p.stem[5:] in judged))
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
    judged = _judged_dates(app)
    window_start = _previous_pack_time(packs, day, judged) or day_start
    events = build_events_between(app.con, window_start, window_end)
    # 既知の事実(新規性なし)とするのは、判定まで終わった日に出たものだけ。判定されなかった日のイベントは、もう一度かける(D42)
    seen = {r["novelty_hash"] for r in app.store.read("event_gate")
            if r.get("novelty_hash") and r.get("date") != day.isoformat() and r.get("date") in judged}
    # シナリオの文から取った語は、広すぎる語(企業・半導体・円安…)だと数百件を『進行中シナリオに関係』で別枠に通してしまう(D42)。
    # このウィンドウで少数のイベントにしか当たらない語だけを、別枠の判定に使う。候補の社名は広くても使う。
    candidate_names = {c.get("master_name") or c["company_name"] for s in active for c in s.candidates}
    statement_words = active_keywords - candidate_names
    haystacks = [e.title + " " + " ".join(e.entities) for e in events]
    from assoc.master.names import CompanyMaster
    company_names = set(CompanyMaster.from_db(app.con).names.values())
    # 文から取った語は、会社名の先頭と一致するもの(例:ソースネクスト・ヤマト・トヨタ)だけ使う。『連日』『円安』『企業』のような
    # 一般語は、少数にしか当たらなくても無関係な記事を別枠に通す(D42。10/17 の試算で野球・宇宙・飲食の記事が通った)
    active_keywords = candidate_names | {w for w in statement_words
                                         if len(w) >= 3 and any(n.startswith(w) for n in company_names)
                                         and sum(1 for h in haystacks if w in h) <= MAX_KEYWORD_HITS}
    hist = _attention_history(app, day)
    from assoc.events.relevance import build_matcher
    from assoc.master.names import CompanyMaster
    relevance = build_matcher(CompanyMaster.from_db(app.con).names, REPO_ROOT / "docs" / "THEMES.md")
    inputs = [GateInput(event_id=e.event_id, title=e.title, novelty_hash=e.novelty_hash,
                        media_count=float(e.media_count), disclosure_type=e.disclosure_type,
                        codes=list(e.codes), entities=list(e.entities),
                        relevance=0 if e.disclosure_type else relevance(e.title)) for e in events]
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
            rows = app.con.execute(
                "SELECT url, source, summary FROM news_item WHERE news_id IN (SELECT unnest(?)) "
                "UNION ALL SELECT url, source, '' FROM disclosure WHERE disclosure_id IN (SELECT unnest(?))",
                [ev.news_ids, ev.disclosure_ids]).fetchall()
            # Google News のリンクは中継 URL で直接開けないことが多い(2026-09-30 予行演習)。
            # 直接開ける URL(NHK・TDnet など)を先に並べ、媒体名も付けて、裏取りの手間を減らす。
            direct = [u for u, _s, _m in rows if "news.google.com" not in u]
            wrapped = [u for u, _s, _m in rows if "news.google.com" in u]
            media = sorted({media_of(s or "", m or "") for _u, s, m in rows} - {""})
            urls = direct + wrapped
            pack_events.append({"event_id": ev.event_id, "title": ev.title, "urls": urls[:5], "media": media[:8],
                                "first_observed_at": to_iso(ev.first_observed_at), "media_count": ev.media_count,
                                "disclosure_type": ev.disclosure_type, "codes": ev.codes,
                                "gate_reason": r.reason, "forced_in": r.forced_in,
                                "attention_high": attention_high})

    themes = _load_themes()
    # テーマ別の見出し件数の急増(足切りは1件ごとの判定なので、別々の会社の話が連日出る話題を取りこぼす。D37)。
    # 急増しているテーマは、代表の見出しをまとめて1つのイベントとして、足切りの枠外で通す。
    bursts = detect_bursts(app.con, day, app.th)
    stock_codes = _theme_stock_codes()
    for b in bursts:
        pack_events.append(_burst_event(app, day, b, themes.get(b.theme_id, b.theme_id), stock_codes.get(b.theme_id, [])))
    burst_by_theme = {b.theme_id: b for b in bursts}
    # 株価の大きな動き(報道の量では拾えない相場の急変。足切りの枠外で通す。D40)
    shocks = _price_shocks(app, day, themes)
    for sh in shocks:
        pack_events.append(_shock_event(app, day, sh, stock_codes.get(sh.shock_id, [])))
    # 産業の力関係を変える新しい報道(媒体数が少なくても通す。D46)
    from assoc.events.structural import detect_structural
    seen_struct = {k for r in app.store.read("event_gate") if r.get("event_id", "").startswith("struct-")
                   and r.get("date") != day.isoformat() for k in r.get("story_keys", [])}
    for hit in detect_structural(app.con, day, seen_keys=seen_struct):
        pack_events.append(_structural_event(app, day, hit, stock_codes.get(hit.theme_id, [])))
    # 辞書に無い話題も拾う:その日に急に増えた語(D39)
    emerging = detect_emerging_terms(app.con, day, app.th, matcher=relevance)
    # 見出し一覧:足切りを通らなかったものも含め、重複をまとめて1行ずつ(強いニュースの選別は LLM が行う。D47)
    from assoc.events.digest import build_digest
    digest = build_digest(events, [e["title"] for e in pack_events])

    cards = latest_by(app.store, "premise_card", "theme_id")
    temps = latest_by(app.store, "theme_temperature", "theme_id")
    # 週1回の LLM の更新の日取りは、機械が書いた温度を除いて数える
    llm_temps = latest_by(_Without(app.store, "auto"), "theme_temperature", "theme_id")
    last_temp = max((r.get("run_date", "") for r in llm_temps.values()), default="")
    temperature_due = not last_temp or (day - date.fromisoformat(last_temp)).days >= 7
    # 急増しているテーマの温度を LLM が3日以上見直していなければ、週1回を待たずに更新の日にする
    for b in bursts:
        prev = llm_temps.get(b.theme_id, {}).get("run_date")
        if not prev or (day - date.fromisoformat(prev)).days >= 3:
            temperature_due = True
    # テーマ別の代表銘柄の騰落率(scripts/theme_returns.py が書く。前提カードの「織り込み度」を数字で補う)
    from assoc.market import theme_returns
    tr_path = theme_returns.RETURNS_PATH
    tr = json.loads(tr_path.read_text(encoding="utf-8")) if tr_path.exists() else {}
    def _price_state(tid: str):
        t = (tr.get("themes") or {}).get(tid)
        if not t or not t.get("n_with_data"):
            return None
        r = lambda x: None if x is None else round(x, 4)
        return {"as_of": tr.get("as_of"), "median_3m": r(t["median_3m"]), "median_1m": r(t["median_1m"]),
                "median_5d": r(t.get("median_5d")),
                "topix_3m": r(tr.get("topix_3m")), "topix_1m": r(tr.get("topix_1m")), "topix_5d": r(tr.get("topix_5d")),
                "stocks": [{"code": x["code"], "name": x["name"], "ret_3m": r(x["ret_3m"]), "ret_5d": r(x.get("ret_5d"))}
                           for x in t["stocks"]]}
    theme_rows = []
    for tid, name in themes.items():
        ps = _price_state(tid)
        b = burst_by_theme.get(tid)
        sig = auto_signal(ps, _burst_dict(b) if b else None, app.th)
        # 機械の判定が、記録の最新の温度と食い違えば、記録に追記する(以降の評価・パックはこの温度を使う)
        if should_write(sig["hint"], temps.get(tid)):
            rec = app.store.append("theme_temperature", {
                "theme_id": tid, "temperature": sig["hint"],
                "reason": "(自動)" + "、".join(sig["reasons"] or ["前回の自動判定を戻す"]),
                "source": "auto", "run_date": day.isoformat(), "model_id": "auto", "prompt_version": "auto-v1",
                "input_pack_hash": None, "source_hash": None})
            temps[tid] = rec
        theme_rows.append({"theme_id": tid, "name": name, "temperature": temps.get(tid, {}).get("temperature"),
                           "temperature_source": (temps[tid].get("source") or "llm") if tid in temps else None,
                           "auto_signal": sig, "price_state": ps,
                           "premise_card": {k: cards[tid][k] for k in ("version", "market_premise", "key_numbers",
                                                                       "breaking_directions")} if tid in cards else None})

    releases_today = [{"scenario_id": s.scenario_id, "arrow_no": no, "indicator": p.get("indicator"),
                       "source": p.get("source")}
                      for s in active for no, p in s.check_plan.items() if p.get("next_release") == day.isoformat()]

    codes = sorted(active_codes | {c for e in pack_events for c in e["codes"]})
    market = _market_state(app, codes, day)

    # 候補ウォッチ:本命・監視の銘柄に、前回の点検以降の新しい材料があるか(Python だけで数える。トークンは使わない)。
    # 深掘りの対象は new_material=true の銘柄だけ。材料が無い銘柄は、調べ直さない。
    from assoc.watch import build_watch
    w = build_watch(app, day)
    watch = {"since": w["since"], "targets": w["targets"], "with_new_material": w["with_new_material"],
             "items": [r for r in w["items"] if r["new_material"]],
             "no_new_material_codes": [r["code"] for r in w["items"] if not r["new_material"]]}
    # 継続性チェック:期待が「注意」「劣化」になった、または状態が変わった銘柄(深掘りの対象。tracking/health.py)
    health = [{"code": r["code"], "company_name": r.get("company_name"), "scenario_id": r["scenario_id"],
               "status": r["status"], "prev_status": r.get("prev_status"), "changed": r.get("changed"),
               "reasons": r.get("reasons", [])}
              for r in app.latest_health(day) if r["status"] in ("注意", "劣化") or r.get("changed")]

    pack = {
        "pack_version": PACK_VERSION, "date": day.isoformat(), "generated_at": to_iso(now), "mode": "通常",
        "window": {"from": to_iso(window_start), "to": to_iso(window_end)},
        "limits": {"max_candidates_per_scenario": app.th.max_candidates_per_scenario,
                   "max_active_scenarios": app.th.max_active_scenarios},
        "events": pack_events,
        # 見出し件数が急増しているテーマ(足切りの別枠。events の中の `theme_burst: true` のイベントがその代表)
        "theme_bursts": [_burst_dict(b) | {"name": themes.get(b.theme_id, b.theme_id)} for b in bursts],
        # 辞書に無い話題を拾うための、その日に急に増えた語(`covered_by` が空のものは、辞書のどのテーマにも当たらない話題)
        "emerging_terms": [{"term": t.term, "today_count": t.today_count, "baseline_per_day": t.baseline,
                            "ratio": t.ratio, "covered_by": t.covered_by,
                            "sample_headlines": [{"title": x["title"], "media": x["media"], "url": x["url"]}
                                                 for x in t.samples[:3]]} for t in emerging],
        "headline_digest": digest,
        "themes": theme_rows,
        "theme_rotation": theme_rotation(tr, themes),
        "unexplained_movers": _unexplained(app, day),
        "macro_drivers": detect_macro_drivers(app.con, day, tr, names=themes),
        "recently_ended_scenarios": _recently_ended(app, day, scenarios, burst_by_theme, shocks),
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
        "watch": watch,
        "health": health,
        "releases_today": releases_today,
        # 週1回、テーマの温度を更新する日かどうか(前回の更新から7日以上たっていれば true)
        "temperature_update_due": temperature_due,
        "temperature_last_updated": last_temp or None,
    }
    body = json.dumps(pack, ensure_ascii=False, sort_keys=True, default=str)
    pack["input_pack_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


class _Without:
    """RecordStore の読み取りだけを包み、`source` が指定の値の行を除く(latest_by に渡すため)。"""

    def __init__(self, store, source: str):
        self._store, self._source = store, source

    def read(self, kind: str):
        return (r for r in self._store.read(kind) if r.get("source") != self._source)


def _theme_stock_codes() -> dict[str, list[str]]:
    from assoc.market.theme_returns import STOCKS_PATH
    if not STOCKS_PATH.exists():
        return {}
    spec = yaml.safe_load(STOCKS_PATH.read_text(encoding="utf-8")) or {}
    return {tid: [str(c) for c in codes] for tid, codes in spec.items()}


def _burst_dict(b: ThemeBurst) -> dict[str, Any]:
    return {"theme_id": b.theme_id, "today_count": b.today_count,
            "baseline_per_day": None if b.baseline is None else round(b.baseline, 1), "ratio": b.ratio,
            "elevated_days": b.elevated_days, "daily": [{"date": d, "count": n} for d, n in b.daily],
            "cold_start": b.cold_start}


def _recently_ended(app: "App", day: date, scenarios, burst_by_theme, shocks, within_days: int = 21) -> list[dict[str, Any]]:
    """「織り込み完了」などで最近終わったシナリオのうち、テーマの報道や株価がまだ強いもの(D40)。

    シナリオの終了は個々の候補の株価の上昇で判定するため、テーマ自体が続いていても終わる。終わったあとに
    テーマが再び強くなっても、進行中のシナリオが無いので、見直しの機会が来ない。その取りこぼしを防ぐため、
    LLM に「出遅れの銘柄・次の段階で新しいシナリオを立てられないか」を見直させる材料として渡す。"""
    import re
    shock_themes = {sh.shock_id: sh for sh in shocks if sh.kind == "theme"}
    out = []
    for st in app.store.read("scenario_status"):
        sid = st.get("scenario_id")
        if st.get("status") != "終了" or sid not in scenarios or not st.get("date"):
            continue
        if (day - date.fromisoformat(st["date"])).days > within_days or st["date"] > day.isoformat():
            continue
        sc = scenarios[sid]
        m = re.match(r"(?:burst|shock)-([A-Z]\d{2})-", getattr(sc, "event_id", "") or "")
        tids = [m.group(1)] if m else list(getattr(sc, "theme_ids", []) or [])
        live = [t for t in tids if t in burst_by_theme or t in shock_themes]
        if not live:
            continue
        out.append({"scenario_id": sid, "statement": sc.statement, "ended_on": st["date"], "end_reason": st.get("reason"),
                    "theme_ids": tids,
                    "theme_burst_now": [{"theme_id": t, "today_count": burst_by_theme[t].today_count,
                                         "elevated_days": burst_by_theme[t].elevated_days}
                                        for t in live if t in burst_by_theme],
                    "theme_price_shock_now": [{"theme_id": t, **shock_themes[t].metrics} for t in live if t in shock_themes],
                    "former_candidates": [{"code": c["code"], "company_name": c.get("master_name") or c["company_name"]}
                                          for c in sc.candidates]})
    return out


def _unexplained(app: "App", day: date) -> dict[str, Any]:
    from assoc.events.unexplained import detect_unexplained_movers
    from assoc.market.layered_prices import gap_path
    from assoc.master.names import CompanyMaster
    try:
        res = detect_unexplained_movers(app.con, gap_path(app.cfg), day, app.th, CompanyMaster.from_db(app.con).names,
                                        app.topix_code())
    except Exception as e:      # 検出に失敗しても、パック全体は作る
        return {"as_of": None, "items": [], "explained_count": 0, "checked": 0, "error": f"{type(e).__name__}: {e}"[:120]}
    stocks = _theme_stock_codes()
    for it in res["items"]:
        it["theme_ids"] = [t for t, codes in stocks.items() if it["code"] in codes]
    return res


def _price_shocks(app: "App", day: date, themes: dict[str, str]):
    from assoc.events.price_shock import detect_price_shocks
    from assoc.market import theme_returns
    from assoc.market.prices import price_source_from_config
    from assoc.pipeline.evaluate import PriceLoader
    tr = json.loads(theme_returns.RETURNS_PATH.read_text(encoding="utf-8")) if theme_returns.RETURNS_PATH.exists() else {}
    try:
        topix = PriceLoader(price_source_from_config(app.cfg), day).get(app.topix_code())
    except ValueError:
        topix = None
    return detect_price_shocks(app.con, day, app.th, tr, topix, themes=themes)


def _shock_event(app: "App", day: date, sh, codes: list[str]) -> dict[str, Any]:
    """株価の急変を1つのイベントにして、足切りの枠外(forced_in)で通す。記録(event_gate)にも残す。"""
    eid = f"shock-{sh.shock_id}-{day.isoformat()}"
    label = "地合い" if sh.kind == "market" else f"{sh.shock_id}"
    title = f"【株価の急変・{label}】{sh.summary}"
    reason = "株価の大きな動き(別枠で通過)"
    app.store.append("event_gate", {"event_id": eid, "date": day.isoformat(), "passed": True, "reason": reason,
                                    "forced_in": True, "novelty_hash": f"shock:{sh.shock_id}:{day.isoformat()}",
                                    "title": title, "attention_high": True,
                                    "attention": {"media_count": float(len(sh.samples)), "gdelt": 0.0, "wiki": 0.0,
                                                  "score": 1.0, "growth": 0.0, "growth_percentile": 1.0}})
    return {"event_id": eid, "title": title, "urls": [i["url"] for i in sh.samples],
            "media": sorted({i["media"] for i in sh.samples} - {""})[:12],
            "first_observed_at": sh.samples[0]["observed_at"] if sh.samples else day.isoformat(),
            "media_count": len(sh.samples), "disclosure_type": "", "codes": codes, "gate_reason": reason,
            "forced_in": True, "attention_high": True, "price_shock": True, "theme_id": sh.shock_id if sh.kind == "theme" else None,
            "price_metrics": sh.metrics,
            "sample_headlines": [{"title": i["title"], "media": i["media"], "url": i["url"]} for i in sh.samples]}


def _structural_event(app: "App", day: date, hit, codes: list[str]) -> dict[str, Any]:
    """構造変化の見出しを1つのイベントにして、足切りの枠外(forced_in)で通す。記録(event_gate)にも残す(D46)。"""
    eid = f"struct-{hit.group_id}-{day.isoformat()}"
    title = f"【構造変化・{hit.theme_id}】{hit.label}({len(hit.samples)}件の見出し)"
    reason = "産業の力関係を変える報道(辞書の語の組み合わせ。別枠で通過)"
    app.store.append("event_gate", {"event_id": eid, "date": day.isoformat(), "passed": True, "reason": reason,
                                    "forced_in": True, "novelty_hash": f"struct:{hit.group_id}:{day.isoformat()}",
                                    "title": title, "attention_high": True, "story_keys": hit.keys,
                                    "attention": {"media_count": float(len(hit.samples)), "gdelt": 0.0, "wiki": 0.0,
                                                  "score": 1.0, "growth": 0.0, "growth_percentile": 1.0}})
    return {"event_id": eid, "title": title, "urls": [i["url"] for i in hit.samples],
            "media": sorted({i["media"] for i in hit.samples} - {""})[:12],
            "first_observed_at": hit.samples[0]["observed_at"] if hit.samples else day.isoformat(),
            "media_count": len(hit.samples), "disclosure_type": "", "codes": codes, "gate_reason": reason,
            "forced_in": True, "attention_high": True, "structural": True, "theme_id": hit.theme_id,
            "sample_headlines": [{"title": i["title"], "media": i["media"], "url": i["url"]} for i in hit.samples]}


def _burst_event(app: "App", day: date, b: ThemeBurst, name: str, codes: list[str]) -> dict[str, Any]:
    """急増したテーマを1つのイベントにして、足切りの枠外(forced_in)で通す。記録(event_gate)にも残す。"""
    eid = f"burst-{b.theme_id}-{day.isoformat()}"
    base = "履歴不足のため件数の目安で判定" if b.cold_start else f"普段 {b.baseline:.0f} 件/日の {b.ratio} 倍"
    title = (f"【{b.theme_id} {name}】関連の見出しが急増(当日 {b.today_count} 件、{base}、"
             f"直近{len(b.daily)}日のうち {b.elevated_days} 日で増加)")
    reason = f"テーマ別の件数が急増(別枠で通過)。日別 {[n for _d, n in b.daily]}"
    app.store.append("event_gate", {"event_id": eid, "date": day.isoformat(), "passed": True, "reason": reason,
                                    "forced_in": True, "novelty_hash": f"burst:{b.theme_id}:{day.isoformat()}",
                                    "title": title, "attention_high": True,
                                    "attention": {"media_count": float(b.today_count), "gdelt": 0.0, "wiki": 0.0,
                                                  "score": 1.0, "growth": b.ratio or 0.0, "growth_percentile": 1.0}})
    return {"event_id": eid, "title": title, "urls": [i["url"] for i in b.samples],
            "media": sorted({i["media"] for i in b.samples} - {""})[:12],
            "first_observed_at": b.samples[0]["observed_at"] if b.samples else day.isoformat(),
            "media_count": b.today_count, "disclosure_type": "", "codes": codes, "gate_reason": reason,
            "forced_in": True, "attention_high": True, "theme_burst": True, "theme_id": b.theme_id,
            "sample_headlines": [{"title": i["title"], "media": i["media"], "url": i["url"]} for i in b.samples]}


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
