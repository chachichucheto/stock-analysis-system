"""開示読解プロンプトの評価(docs/VALUE_DESIGN.md §8)。

評価セット(knowledge/value_eval/*.yaml)の「正解」と、プロンプトの出力を比べる。
`fictional: true` の事例は、形式の見本と評価器の自己テスト用。**本番の精度の集計には含めない**。
本物の開示と事後の株価の動きから作る評価セットは、ローカルで作る(docs/VALUE_LOCAL_KICKOFF.md フェーズC)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from assoc.value.catalyst import _norm
from assoc.value.score import STRENGTHS

REQUIRED = ("id", "input", "expected")


def load_cases(directory: Path) -> list[dict[str, Any]]:
    cases = []
    for p in sorted(Path(directory).glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        missing = [k for k in REQUIRED if k not in d]
        if missing:
            raise ValueError(f"{p.name}: 必要な項目がありません: {missing}")
        d["_file"] = p.name
        cases.append(d)
    ids = [c["id"] for c in cases]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"評価セットの id が重複しています: {sorted(dup)}")
    return cases


def case_texts(case: dict) -> dict[str, str]:
    """{source_id: タイトル+本文}。引用の照合に使う。"""
    return {d["id"]: f"{d.get('title', '')}\n{d.get('body_excerpt', '')}" for d in case["input"]["disclosures"]}


def score_case(case: dict, item: dict | None) -> dict[str, Any]:
    exp = case["expected"]
    if item is None:
        return {"id": case["id"], "answered": False}
    exp_t, act_t = set(exp.get("catalyst_types", [])), set(item["catalyst_types"])
    tp = len(exp_t & act_t)
    precision = tp / len(act_t) if act_t else (1.0 if not exp_t else 0.0)
    recall = tp / len(exp_t) if exp_t else (1.0 if not act_t else 0.0)
    gap = abs(STRENGTHS.index(exp["strength"]) - STRENGTHS.index(item["strength"]))
    texts = case_texts(case)
    quotes_ok = all(f["source_id"] in texts and _norm(f["quote"]) in _norm(texts[f["source_id"]]) for f in item["facts"])
    return {"id": case["id"], "answered": True, "precision": precision, "recall": recall,
            "strength_exact": gap == 0, "strength_within_one": gap <= 1,
            "bear_case_present": bool(item["bear_case"].strip()) or item["strength"] == "None",
            "quotes_grounded": quotes_ok,
            # 強い材料を「なし」にした、またはその逆(重大な取り違え)
            "severe_miss": (exp["strength"] in ("Strong", "Medium")) != (item["strength"] in ("Strong", "Medium"))}


def summarize(results: list[dict[str, Any]], cases: list[dict], include_fictional: bool = False) -> dict[str, Any]:
    fict = {c["id"] for c in cases if c.get("fictional")}
    rows = [r for r in results if include_fictional or r["id"] not in fict]
    answered = [r for r in rows if r["answered"]]
    n = len(rows)
    if n == 0:
        return {"n": 0, "note": "本番の評価対象(fictional でない事例)がありません"}

    def mean(key: str) -> float | None:
        return sum(float(r[key]) for r in answered) / len(answered) if answered else None

    return {"n": n, "answered": len(answered), "precision": mean("precision"), "recall": mean("recall"),
            "strength_exact": mean("strength_exact"), "strength_within_one": mean("strength_within_one"),
            "bear_case_present": mean("bear_case_present"), "quotes_grounded": mean("quotes_grounded"),
            "severe_miss": mean("severe_miss"),
            "small_sample": n < 30}


def run_eval(cases: list[dict], outputs: dict) -> tuple[list[dict], dict]:
    by_key = {it["key"]: it for it in outputs["items"]}
    results = [score_case(c, by_key.get(c["id"])) for c in cases]
    return results, summarize(results, cases)
