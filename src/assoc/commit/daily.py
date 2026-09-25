"""夜の手順の出力を検証して記録する(docs/DESIGN.md §6.5)。

LLM の出力(data/inbox/*.json)をそのまま信用しない。次を確かめてから追記する。
- JSON スキーマに合っているか
- 銘柄コードと社名が銘柄マスタと合うか(合わない候補は確定しない)
- 「確認」とされた矢印に、信頼度1・2の支持する証拠があるか(無ければ「未確認」に直す)
- 既存シナリオへの更新が、実在するシナリオを指しているか
- 同じ出力を二重に確定しようとしていないか
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from assoc.config import REPO_ROOT, Thresholds
from assoc.master.names import CompanyMaster
from assoc.store.records import RecordStore

SCHEMA_PATH = REPO_ROOT / "schemas" / "daily_output.schema.json"
STRENGTHS = ["Weak", "Weak+", "Medium", "Strong"]


@dataclass
class CommitResult:
    ok: bool
    source_hash: str
    errors: list[str] = field(default_factory=list)       # 確定を止めた理由
    warnings: list[str] = field(default_factory=list)     # 確定したが直した・落とした点
    scenario_ids: dict[str, str] = field(default_factory=dict)   # scenario_ref -> 正式な番号
    counts: dict[str, int] = field(default_factory=dict)


def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def validate_schema(doc: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema(), format_checker=jsonschema.FormatChecker())
    return [f"{'/'.join(map(str, e.absolute_path)) or '(全体)'}: {e.message}"
            for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))]


def _already_committed(store: RecordStore, source_hash: str) -> bool:
    return any(r.get("source_hash") == source_hash for r in store.read("grade")) or \
        any(r.get("source_hash") == source_hash for r in store.read("scenario_update"))


def _next_scenario_number(store: RecordStore) -> int:
    ids = {r["scenario_id"] for r in store.read("scenario")}
    return len(ids) + 1


def commit_daily(doc: dict[str, Any], store: RecordStore, master: CompanyMaster,
                 thresholds: Thresholds = Thresholds(), source_hash: str | None = None) -> CommitResult:
    source_hash = source_hash or hashlib.sha256(
        json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    result = CommitResult(ok=False, source_hash=source_hash)

    result.errors = validate_schema(doc)
    if not result.errors and _already_committed(store, source_hash):
        result.errors.append("この出力はすでに確定済みです")
    if result.errors:
        store.append("commit_rejection", {"source_hash": source_hash, "errors": result.errors})
        return result

    run = doc["run"]
    common = {"source_hash": source_hash, "run_date": run["date"], "model_id": run["model_id"],
              "prompt_version": run["prompt_version"], "input_pack_hash": run["input_pack_hash"]}
    known_scenarios = {r["scenario_id"] for r in store.read("scenario")}

    # 新しいシナリオの番号を先に決める(証拠や確認状況が scenario_ref で参照するため)
    n = _next_scenario_number(store)
    for sc in doc["scenarios"]:
        result.scenario_ids[sc["scenario_ref"]] = f"SC{n:04d}"
        n += 1

    def resolve(ref: str) -> str | None:
        if ref in result.scenario_ids:
            return result.scenario_ids[ref]
        return ref if ref in known_scenarios else None

    counts = {k: 0 for k in ("premise_card", "grade", "scenario", "candidate", "check_plan",
                             "evidence", "scenario_update", "theme_temperature")}

    for card in doc.get("premise_cards", []):
        store.append("premise_card", {**common, **card})
        counts["premise_card"] += 1

    for g in doc["grades"]:
        store.append("grade", {**common, **g})
        counts["grade"] += 1

    for sc in doc["scenarios"]:
        sid = result.scenario_ids[sc["scenario_ref"]]
        er = sc["expected_rise"]
        if not er["low"] <= er["median"] <= er["high"]:
            result.warnings.append(f"{sid}: 想定上昇幅の下限・中央・上限の順序が不正のため、並べ替えて記録")
            er = {**er, **dict(zip(("low", "median", "high"), sorted([er["low"], er["median"], er["high"]])))}
        days = sc["expected_days"]
        if sc["time_type"] == "単発":
            clamped = min(max(days, thresholds.min_expected_days), thresholds.max_expected_days)
            if clamped != days:
                result.warnings.append(f"{sid}: 単発型の想定期間 {days} を {clamped} 営業日に補正")
                days = clamped

        accepted, rejected = [], list(sc["rejected_candidates"])
        for c in sc["candidates"]:
            check = master.check(c["code"], c["company_name"])
            if check.ok:
                accepted.append({**c, "master_name": check.master_name})
            else:
                result.warnings.append(f"{sid}: 候補 {c['code']} {c['company_name']} を確定しない({check.reason})")
                rejected.append({"code": c["code"], "company_name": c["company_name"],
                                 "reason": f"照合不一致: {check.reason}"})
        if not accepted:
            result.warnings.append(f"{sid}: 確定できる候補が無いため、候補なしのシナリオとして記録")

        body = {k: v for k, v in sc.items() if k not in ("candidates", "rejected_candidates", "check_plan", "scenario_ref")}
        store.append("scenario", {**common, **body, "scenario_id": sid, "scenario_ref": sc["scenario_ref"],
                                  "version": 1, "expected_rise": {**er, "version": 1},
                                  "expected_days": days,
                                  "status": "進行中" if sc["started"] else "待機",
                                  "rejected_candidates": rejected})
        counts["scenario"] += 1
        for plan in sc["check_plan"]:
            store.append("check_plan", {**common, "scenario_id": sid, **plan, "status": "未確認"})
            counts["check_plan"] += 1
        for i, c in enumerate(accepted, 1):
            store.append("candidate", {**common, "scenario_id": sid, "candidate_id": f"{sid}-{i}", **c})
            counts["candidate"] += 1

    for up in doc["scenario_updates"]:
        if up["scenario_id"] not in known_scenarios:
            result.warnings.append(f"更新先のシナリオ {up['scenario_id']} が存在しないため、記録しない")
            continue
        store.append("scenario_update", {**common, **up})
        counts["scenario_update"] += 1

    # 証拠:参照先を正式な番号にする
    strong_support: set[tuple[str, int]] = {
        (r["scenario_id"], r["arrow_no"]) for r in store.read("evidence")
        if r.get("supports") == "for" and r.get("source_tier", 9) <= 2}
    for ev in doc["evidence"]:
        sid = resolve(ev["scenario"])
        if sid is None:
            result.warnings.append(f"証拠の参照先 {ev['scenario']} が不明なため、記録しない")
            continue
        store.append("evidence", {**common, **{k: v for k, v in ev.items() if k != "scenario"}, "scenario_id": sid})
        counts["evidence"] += 1
        if ev["supports"] == "for" and ev["source_tier"] <= 2:
            strong_support.add((sid, ev["arrow_no"]))

    # 確認状況:信頼度1・2の支持する証拠が無い「確認」は「未確認」に直す(CONCEPT §7.3)
    for st in doc["check_status"]:
        sid = resolve(st["scenario"])
        if sid is None:
            result.warnings.append(f"確認状況の参照先 {st['scenario']} が不明なため、記録しない")
            continue
        status = st["status"]
        if status == "確認" and (sid, st["arrow_no"]) not in strong_support:
            result.warnings.append(f"{sid} 矢印{st['arrow_no']}: 信頼度1・2の証拠が無いため「未確認」に修正")
            status = "未確認"
        store.append("check_plan", {**common, "scenario_id": sid, "arrow_no": st["arrow_no"],
                                    "status": status, "status_update": True})

    for t in doc.get("theme_temperatures", []):
        store.append("theme_temperature", {**common, **t})
        counts["theme_temperature"] += 1

    result.ok = True
    result.counts = counts
    return result


def commit_file(path: Path, store: RecordStore, master: CompanyMaster,
                thresholds: Thresholds = Thresholds()) -> CommitResult:
    """inbox のファイルを確定し、確定できたら processed/ に移す。"""
    raw = Path(path).read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        store.append("commit_rejection", {"source_hash": source_hash, "errors": [f"JSON として読めません: {e}"]})
        return CommitResult(ok=False, source_hash=source_hash, errors=[f"JSON として読めません: {e}"])
    result = commit_daily(doc, store, master, thresholds, source_hash)
    if result.ok:
        done = Path(path).parent / "processed"
        done.mkdir(exist_ok=True)
        shutil.move(str(path), str(done / Path(path).name))
    return result
