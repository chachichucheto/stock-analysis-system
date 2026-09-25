"""過去事例ライブラリの値動きを株価データから計算する(knowledge/past_cases/README.md)。

値動き(max_excess_20d・moved・days_to_move)は手で書かず、ここで計算して YAML に書き戻す。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import yaml

from assoc.config import REPO_ROOT
from assoc.market.indicators import excess_return
from assoc.market.prices import price_source_from_config
from assoc.timeutil import add_business_days, business_days_between

if TYPE_CHECKING:
    from assoc.app import App


def compute_case(case: dict, source, topix_code: str, moved_excess: float, start_excess: float) -> list[str]:
    event_date = case["event_date"] if isinstance(case["event_date"], date) else date.fromisoformat(str(case["event_date"]))
    end = add_business_days(event_date, 20)
    topix = source.daily(topix_code, event_date - timedelta(days=10), end)
    notes = []
    for rel in case.get("related", []):
        prices = source.daily(str(rel["code"]), event_date - timedelta(days=10), end)
        if prices.empty or topix.empty:
            notes.append(f"{case['case_id']} {rel['code']}: 株価が無いため計算しない")
            continue
        best, first_move = 0.0, None
        for d in prices["date"]:
            if event_date <= d <= end:
                x = excess_return(prices, topix, event_date, d)
                best = max(best, x)
                if first_move is None and x >= start_excess:
                    first_move = d
        rel["max_excess_20d"] = round(best, 4)
        rel["moved"] = best >= moved_excess
        rel["days_to_move"] = business_days_between(event_date, first_move) if first_move else None
    return notes


def check_codes(case: dict, master) -> list[str]:
    """過去事例の銘柄コードと社名を、銘柄マスタと照合する(LLM の取り違え対策。上場廃止銘柄はマスタに無い)。"""
    notes = []
    for rel in case.get("related", []):
        r = master.check(str(rel["code"]), rel.get("company_name", ""))
        if not r.ok:
            notes.append(f"{case['case_id']} {rel['code']} {rel.get('company_name', '')}: {r.reason}")
    return notes


def compute_all(app: "App") -> list[str]:
    from assoc.master.names import CompanyMaster
    source = price_source_from_config(app.cfg)
    master = CompanyMaster.from_db(app.con, REPO_ROOT / "knowledge" / "aliases.yaml")
    out = []
    for f in sorted((REPO_ROOT / "knowledge" / "past_cases").glob("PC*.yaml")):
        case = yaml.safe_load(f.read_text(encoding="utf-8"))
        if len(master):
            out += [f"⚠ 照合: {n}" for n in check_codes(case, master)]
        out += compute_case(case, source, app.topix_code(), app.th.moved_excess, app.th.move_start_excess)
        f.write_text(yaml.safe_dump(case, allow_unicode=True, sort_keys=False), encoding="utf-8")
        out.append(f"{f.name}: 計算しました")
    return out or ["過去事例がまだありません(knowledge/past_cases/PC*.yaml)"]
