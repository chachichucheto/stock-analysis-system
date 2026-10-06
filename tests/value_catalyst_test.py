import copy
from pathlib import Path

import pytest

from assoc.value import catalyst as cat
from assoc.value import evalset

SOURCES = {"1000": {"d1": "自己株式の取得に関するお知らせ\n取得し得る株式の総数 600,000株(発行済株式の6.0%)"}}


def item(**kw):
    base = {"key": "1000", "catalyst_types": ["株主還元"], "strength": "Medium", "timing": "1〜3か月",
            "summary": "自己株式取得", "bull_case": "需給の改善", "bear_case": "規模が小さく効果は限定的",
            "facts": [{"text": "発行済株式の6%を取得", "source_id": "d1", "quote": "取得し得る株式の総数 600,000株"}],
            "confirm_next": "取得枠の消化率"}
    base.update(kw)
    return base


def out(*items):
    return {"asof": "2026-10-06", "items": list(items)}


def test_valid_output_passes():
    assert cat.validate_output(out(item()), sources=SOURCES, allowed_keys={"1000"}) == []


def test_quote_must_appear_in_the_source_text():
    bad = item(facts=[{"text": "x", "source_id": "d1", "quote": "取得総額 50億円"}])
    errs = cat.validate_output(out(bad), sources=SOURCES)
    assert any("根拠なし" in e for e in errs)


def test_quote_matching_ignores_only_whitespace():
    ok = item(facts=[{"text": "x", "source_id": "d1", "quote": "取得し得る株式の総数\n 600,000株"}])
    assert cat.validate_output(out(ok), sources=SOURCES) == []


def test_unknown_source_id_and_unknown_key_are_errors():
    bad = item(facts=[{"text": "x", "source_id": "d9", "quote": "q"}])
    assert any("入力にありません" in e for e in cat.validate_output(out(bad), sources=SOURCES))
    assert any("入力パックにない" in e for e in cat.validate_output(out(item(key="9999")), allowed_keys={"1000"}))


def test_bear_case_is_mandatory_for_positive_calls():
    errs = cat.validate_output(out(item(bear_case="  ")))
    assert any("反対仮説" in e for e in errs)


def test_none_strength_rules():
    none_ok = item(catalyst_types=[], strength="None", facts=[], bear_case="")
    assert cat.validate_output(out(none_ok)) == []
    assert cat.validate_output(out(item(catalyst_types=["業績"], strength="None")))      # None なのに種類がある
    assert cat.validate_output(out(item(catalyst_types=[], strength="Strong")))          # 種類がない
    assert cat.validate_output(out(item(facts=[])))                                      # 根拠がない


def test_schema_rejects_bad_enum_duplicates_and_extra_fields():
    assert cat.validate_output(out(item(strength="High")))
    assert cat.validate_output(out(item(), item()))                                      # key 重複
    bad = item()
    bad["extra"] = 1
    assert cat.validate_output(out(bad))
    assert cat.validate_output({"asof": "2026/10/06", "items": []})


def test_to_catalysts():
    c = cat.to_catalysts(out(item()))["1000"]
    assert c.strength == "Medium" and c.types == ["株主還元"] and c.bear_case


# ---- 評価セット ----

EVAL_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "value_eval"


def test_shipped_samples_load_and_are_marked_fictional():
    cases = evalset.load_cases(EVAL_DIR)
    assert len(cases) >= 4 and all(c["fictional"] for c in cases)


def test_every_sample_source_exists_and_expected_is_consistent():
    for c in evalset.load_cases(EVAL_DIR):
        assert c["input"]["disclosures"]
        exp = c["expected"]
        assert (exp["strength"] == "None") == (not exp["catalyst_types"])
        assert set(exp["catalyst_types"]) <= set(cat.CATALYST_TYPES)


def _answer(case, **kw):
    base = {"key": case["id"], "catalyst_types": list(case["expected"]["catalyst_types"]),
            "strength": case["expected"]["strength"], "timing": "不明", "summary": "s", "bull_case": "b",
            "bear_case": "反対仮説", "facts": [], "confirm_next": "c"}
    if base["strength"] != "None":
        d = case["input"]["disclosures"][0]
        base["facts"] = [{"text": "t", "source_id": d["id"], "quote": d["title"]}]
    base.update(kw)
    return base


def test_perfect_answers_score_perfectly_but_fictional_cases_are_excluded_from_summary():
    cases = evalset.load_cases(EVAL_DIR)
    outputs = {"asof": "2026-10-06", "items": [_answer(c) for c in cases]}
    assert cat.validate_output(outputs) == []
    results, summary = evalset.run_eval(cases, outputs)
    assert all(r["precision"] == 1 and r["recall"] == 1 and r["strength_exact"] for r in results)
    assert summary["n"] == 0 and "fictional" in summary["note"]             # 見本は本番の精度に数えない


def test_summary_counts_real_cases_and_flags_small_sample():
    cases = copy.deepcopy(evalset.load_cases(EVAL_DIR))
    for c in cases:
        c["fictional"] = False
    outputs = {"asof": "2026-10-06", "items": [_answer(cases[0], catalyst_types=["業績"]),     # 種類を取り違え
                                               _answer(cases[2], strength="None", catalyst_types=[], facts=[], bear_case="")]}
    results, summary = evalset.run_eval(cases, outputs)
    by_id = {r["id"]: r for r in results}
    assert by_id[cases[0]["id"]]["precision"] == 0 and by_id[cases[0]["id"]]["recall"] == 0
    assert by_id[cases[2]["id"]]["severe_miss"] is True                                         # Strong を None と答えた
    assert by_id[cases[1]["id"]]["answered"] is False
    assert summary["n"] == len(cases) and summary["answered"] == 2 and summary["small_sample"] is True


def test_ungrounded_quote_is_detected_by_the_evaluator():
    cases = evalset.load_cases(EVAL_DIR)
    c = cases[0]
    r = evalset.score_case(c, _answer(c, facts=[{"text": "t", "source_id": "d1", "quote": "存在しない文面"}]))
    assert r["quotes_grounded"] is False


def test_duplicate_ids_and_missing_fields_are_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text("id: X\ninput: {disclosures: []}\nexpected: {strength: None}\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("id: X\ninput: {disclosures: []}\nexpected: {strength: None}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重複"):
        evalset.load_cases(tmp_path)
    (tmp_path / "b.yaml").write_text("id: Y\ninput: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="必要な項目"):
        evalset.load_cases(tmp_path)
