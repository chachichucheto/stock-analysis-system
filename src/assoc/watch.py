"""候補ウォッチ(L2):本命・監視の銘柄だけを対象に、前回の調査以降の「新しい材料」を自動で数える。

目的(docs/PROPOSAL_HYBRID_AND_SHARING.md §1):トークンを使う深掘り(夜の Claude の Web 検索)を、
「新しい材料があった候補」だけに絞る。ここは Python だけで動き、トークンを使わない。

対象銘柄:進行中・待機中のシナリオの候補と、本命に挙げた銘柄(pick_tracking の first_pick)。
新しい材料(前回の点検 `since` 以降):
- 開示:その銘柄コードの TDnet・EDINET の新しい開示
- ニュース:見出しに社名(またはコード)を含む記事(RSS・Google News・PR TIMES)
- 決算:決算発表予定日が7日以内
`new_material` が True の銘柄だけ、夜の深掘りの対象にする。False の銘柄は調べ直さない。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assoc.events.cluster import media_of
from assoc.events.gate import is_strong_disclosure
from assoc.timeutil import to_iso, utcnow

if TYPE_CHECKING:
    from assoc.app import App

MAX_ITEMS = 8              # 銘柄ごとに載せる見出しの上限(パックを大きくしないため)
MIN_NAME_LEN = 3           # 社名がこれより短いときは、社名の一致だけでは数えない(誤検出が多いため)
EARNINGS_SOON_DAYS = 7


def targets_from_app(app: "App") -> list[dict[str, Any]]:
    """対象銘柄の一覧: [{code, company_name, scenario_ids}]。"""
    from assoc.state import load_scenarios

    out: dict[str, dict[str, Any]] = {}
    for s in load_scenarios(app.store).values():
        if not s.is_active:
            continue
        for c in s.candidates:
            t = out.setdefault(c["code"], {"code": c["code"], "scenario_ids": [],
                                           "company_name": c.get("master_name") or c["company_name"]})
            if s.scenario_id not in t["scenario_ids"]:
                t["scenario_ids"].append(s.scenario_id)
    for p in app.store.read("pick_tracking"):
        if p.get("event") == "first_pick" and p.get("code"):
            t = out.setdefault(p["code"], {"code": p["code"], "scenario_ids": [],
                                           "company_name": p.get("company_name") or p["code"]})
            if p.get("scenario_id") and p["scenario_id"] not in t["scenario_ids"]:
                t["scenario_ids"].append(p["scenario_id"])
    return list(out.values())


def scan(con, targets: list[dict[str, Any]], since: datetime, today: date) -> list[dict[str, Any]]:
    """各対象銘柄について、since 以降の新しい材料を数える。"""
    results = []
    for t in targets:
        code, name = t["code"], t.get("company_name") or ""
        disclosures = [{"title": r[0], "url": r[1], "source": r[2], "strong": is_strong_disclosure(r[0] or ""),
                        "published_at": to_iso(r[3]) if r[3] else None}
                       for r in con.execute(
                           "SELECT title, url, source, published_at FROM disclosure "
                           "WHERE code = ? AND first_observed_at > ? ORDER BY first_observed_at DESC LIMIT ?",
                           [code, since, MAX_ITEMS]).fetchall()]
        needles = [code] if code else []
        if len(name) >= MIN_NAME_LEN:
            needles.append(name)
        news = []
        if needles:
            cond = " OR ".join(["title LIKE ?"] * len(needles))
            rows = con.execute(
                f"SELECT title, url, source, summary FROM news_item WHERE first_observed_at > ? AND ({cond}) "
                f"ORDER BY first_observed_at DESC LIMIT ?",
                [since] + [f"%{n}%" for n in needles] + [MAX_ITEMS * 3]).fetchall()
            seen_titles: set[str] = set()
            for title, url, source, summary in rows:
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                news.append({"title": title, "url": url, "media": media_of(source or "", summary or "")})
            news = news[:MAX_ITEMS]
        cal = con.execute("SELECT earnings_date FROM calendar WHERE code = ?", [code]).fetchone()
        earnings_soon = bool(cal and cal[0] and 0 <= (cal[0] - today).days <= EARNINGS_SOON_DAYS)
        results.append({
            "code": code, "company_name": name, "scenario_ids": t.get("scenario_ids", []),
            "new_material": bool(disclosures or news or earnings_soon),
            "disclosures": disclosures, "news": news,
            "earnings_date": cal[0].isoformat() if cal and cal[0] else None, "earnings_soon": earnings_soon,
        })
    return results


def _state_path(app: "App") -> Path:
    return app.dir("watch") / "state.json"


def last_checked(app: "App", default_hours: int = 24) -> datetime:
    p = _state_path(app)
    if p.exists():
        try:
            from assoc.timeutil import parse_iso
            return parse_iso(json.loads(p.read_text(encoding="utf-8"))["checked_at"])
        except (KeyError, ValueError):
            pass
    return utcnow() - timedelta(hours=default_hours)


def build_watch(app: "App", day: date, *, save_state: bool = True) -> dict[str, Any]:
    """対象銘柄を点検し、data/watch/watch_YYYY-MM-DD.json に書く。パックに載せる要約を返す。"""
    since = last_checked(app)
    now = utcnow()
    targets = targets_from_app(app)
    rows = scan(app.con, targets, since, day)
    out = {"date": day.isoformat(), "since": to_iso(since), "checked_at": to_iso(now),
           "targets": len(targets), "with_new_material": sum(r["new_material"] for r in rows), "items": rows}
    (app.dir("watch") / f"watch_{day.isoformat()}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if save_state:
        _state_path(app).write_text(json.dumps({"checked_at": to_iso(now)}), encoding="utf-8")
    return out
