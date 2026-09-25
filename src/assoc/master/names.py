"""銘柄コードと社名の照合(docs/DESIGN.md §6.5)。

LLM は銘柄コードを取り違えることがある(THEMES.md §6 の実例)。候補はコードと社名の両方が
銘柄マスタと合うときだけ確定する。マスタは上場銘柄一覧(universe_daily)の最新日と、
knowledge/aliases.yaml の別名辞書から作る。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

_LEGAL_FORMS = re.compile(r"(株式会社|\(株\)|（株）|㈱|有限会社|合同会社)")
_SPACES = re.compile(r"[\s・･\.\-‐－ー_]")


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    s = _LEGAL_FORMS.sub("", s)
    s = _SPACES.sub("", s)
    return s.lower()


@dataclass(frozen=True)
class NameCheck:
    ok: bool
    reason: str
    master_name: str | None = None


class CompanyMaster:
    def __init__(self, names: dict[str, str], aliases: dict[str, str] | None = None):
        self.names = names                                  # code -> 正式社名
        self.aliases = {normalize_name(k): v for k, v in (aliases or {}).items()}  # 別名 -> code

    @classmethod
    def from_db(cls, con, aliases_file: Path | None = None) -> "CompanyMaster":
        rows = con.execute(
            "SELECT code, company_name FROM universe_daily "
            "WHERE date = (SELECT max(date) FROM universe_daily)").fetchall()
        aliases = {}
        if aliases_file and Path(aliases_file).exists():
            with Path(aliases_file).open(encoding="utf-8") as f:
                aliases = {str(k): str(v) for k, v in (yaml.safe_load(f) or {}).items()}
        return cls({code: name for code, name in rows}, aliases)

    def __len__(self) -> int:
        return len(self.names)

    def check(self, code: str, name: str) -> NameCheck:
        if not self.names:
            return NameCheck(False, "銘柄マスタが空です(上場銘柄一覧を先に収集してください)")
        master = self.names.get(code)
        if master is None:
            return NameCheck(False, f"銘柄コード {code} は上場銘柄一覧にありません")
        given, official = normalize_name(name), normalize_name(master)
        if len(given) >= 2 and (given == official or given in official or official in given):
            return NameCheck(True, "一致", master)
        if self.aliases.get(given) == code:
            return NameCheck(True, "別名辞書で一致", master)
        return NameCheck(False, f"社名が一致しません(出力: {name} / マスタ: {master})", master)
