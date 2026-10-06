"""開示読解の出力(LLM)の検証と取り込み(docs/VALUE_DESIGN.md §7)。

LLM の推論は証拠ではない(DECISIONS D04)。そのため、根拠の引用(quote)が入力の開示の文面に
**そのまま含まれているか**を機械的に確かめる。含まれない引用は「根拠なし」として弾く。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from assoc.config import REPO_ROOT
from assoc.value.score import Catalyst

CATALYST_TYPES = ("株主還元", "資産・資本", "株主の動き", "業績", "市場区分・指数", "事業")
SCHEMA_PATH = REPO_ROOT / "schemas" / "value_catalyst_output.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    """引用の照合用:空白と改行の違いだけ吸収する(語句は変えない)。"""
    return "".join(s.split())


def validate_output(obj: Any, sources: dict[str, dict[str, str]] | None = None,
                    allowed_keys: set[str] | None = None) -> list[str]:
    """エラーの一覧を返す(空なら通る)。

    sources:  {key: {source_id: 文面}}。渡すと、引用が文面に含まれるか、source_id が実在するかを確かめる。
    allowed_keys: 入力パックにある key。これ以外の key はエラー。
    """
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(全体)'}: {e.message}"
              for e in jsonschema.Draft202012Validator(load_schema()).iter_errors(obj)]
    if errors:
        return errors
    seen: set[str] = set()
    for i, it in enumerate(obj["items"]):
        where = f"items[{i}] key={it['key']}"
        if it["key"] in seen:
            errors.append(f"{where}: key が重複しています")
        seen.add(it["key"])
        if allowed_keys is not None and it["key"] not in allowed_keys:
            errors.append(f"{where}: 入力パックにない key です")
        if it["strength"] != "None":
            if not it["catalyst_types"]:
                errors.append(f"{where}: strength が None 以外なのに catalyst_types が空です")
            if not it["bear_case"].strip():
                errors.append(f"{where}: bear_case(反対仮説)は必須です。強化と弱体化の両方を書いてください")
            if not it["facts"]:
                errors.append(f"{where}: strength が None 以外なのに facts(根拠)がありません")
        else:
            if it["catalyst_types"]:
                errors.append(f"{where}: strength が None なのに catalyst_types があります")
        if sources is not None:
            docs = sources.get(it["key"], {})
            for j, fact in enumerate(it["facts"]):
                src = docs.get(fact["source_id"])
                if src is None:
                    errors.append(f"{where}: facts[{j}].source_id {fact['source_id']!r} は入力にありません")
                elif _norm(fact["quote"]) not in _norm(src):
                    errors.append(f"{where}: facts[{j}].quote が開示の文面にそのまま含まれていません(根拠なし)")
    return errors


def to_catalysts(obj: dict) -> dict[str, Catalyst]:
    return {it["key"]: Catalyst(types=list(it["catalyst_types"]), strength=it["strength"], timing=it["timing"],
                                summary=it["summary"], bull_case=it["bull_case"], bear_case=it["bear_case"],
                                confirm_next=it["confirm_next"]) for it in obj["items"]}


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
