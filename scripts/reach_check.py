"""連想の答え合わせ(D50-C)。過去の各日に、材料の重なった銘柄の連想先が、その後 h 営業日で動いたかを測る。

使い方: ./.venv/Scripts/python.exe scripts/reach_check.py [--from 2026-10-05] [--to 2026-10-22] [--horizon 5] [--thresh 0.05]
株価は答え合わせにだけ使う(検知・連想には使わない)。連想の地図は、その日までに分かっていた記録だけで作る。
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from assoc.app import App
from assoc.config import load_config
from assoc.events.companies import CompanyIndex
from assoc.events.ledger import build_ledger, collect_materials
from assoc.market.layered_prices import gap_path
from assoc.pack import build as B
from assoc.review import reach


NO_CURATED = True      # 過去の日の答え合わせでは、手で書いた関係を使わない(--with-curated で使う)


def main() -> None:
    global NO_CURATED
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-curated", action="store_true")
    ap.add_argument("--from", dest="start", default="2026-10-05")
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--horizon", type=int, default=reach.HORIZON)
    ap.add_argument("--thresh", type=float, default=reach.THRESH)
    a = ap.parse_args()
    NO_CURATED = not a.with_curated
    app = App(load_config(None))
    hist = (app.cfg.section("prices").get("layered") or {}).get("history")
    closes = reach.load_closes(hist, gap_path(app.cfg))
    index = CompanyIndex.from_db(app.con, B.REPO_ROOT / "knowledge" / "aliases.yaml",
                                 B.REPO_ROOT / "knowledge" / "company_name_stop.yaml")
    names = B.CompanyMaster.from_db(app.con).names if hasattr(B, "CompanyMaster") else {}
    if not names:
        from assoc.master.names import CompanyMaster
        names = CompanyMaster.from_db(app.con).names
    cand_all = list(app.store.read("candidate"))
    day, end = date.fromisoformat(a.start), date.fromisoformat(a.end) if a.end else app.today()
    scores = []
    while day <= end:
        excess = reach.forward_excess(closes, day, a.horizon)
        if excess is not None:
            mats = collect_materials(app.con, index, day)
            ledger = build_ledger(app.con, index, day, liquid=None, names=names, by_code=mats)
            links = _links(app, index, day, [r for r in cand_all if r.get("run_date", "") <= day.isoformat()])
            for e in ledger:
                rel = [r["code"] for r in links.related(e["code"], limit=6)]
                s = reach.score_case(day.isoformat(), e["code"], rel, excess, a.thresh)
                scores.append(s)
                print(day, e["code"], names.get(e["code"], ""), f"連想先{s.related}件 当たり{s.hits}件 基準率{s.base_rate:.0%}",
                      "" if s.lift is None else f"lift={s.lift:.1f}",
                      "" if s.own_excess is None else f"本人{s.own_excess:+.1%}")
        day += timedelta(days=1)
    print(reach.summarize(scores))


def _links(app, index, day, cand_rows):
    from assoc.events import links as L
    from datetime import datetime, time
    lm = L.LinkMap()
    L.add_groups(lm, index, {})
    if not NO_CURATED:      # 手で書いた関係は、書いた日以降の株価の反応を根拠にしたものがある。過去の日に当てると答えが漏れる
        L.add_curated(lm, B.REPO_ROOT / "knowledge" / "company_links.yaml")
    L.add_scenarios(lm, cand_rows)
    L.add_themes(lm, B._theme_stock_codes(), B._load_themes())
    start = datetime.combine(day - timedelta(days=13), time.min, tzinfo=B.JST)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=B.JST)
    rows = app.con.execute("SELECT title || ' ' || coalesce(summary, '') FROM news_item "
                           "WHERE first_observed_at >= ? AND first_observed_at < ?", [start, end]).fetchall()
    L.add_co_mentions(lm, index, (r[0] for r in rows))
    return lm


if __name__ == "__main__":
    main()
