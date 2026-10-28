"""見出しに出てくる上場企業の特定と、同じ会社の話のまとめ(D48)。

クラスタリング(cluster.py)は見出しの文字2-gramと漢字・カタカナの連続の重なりで同じ出来事をまとめるが、
言い回しが割れると別々のイベントに分かれ、1つの話が媒体数1〜2のイベントに砕ける(GMOの不正アクセスは10/6〜10/28に
約48件に砕けて、すべて足切りで落ちた)。また英字の社名(GMO・NTT・KDDI)は固有表現として拾えない。
ここでは銘柄マスタ(universe_daily の社名と aliases.yaml)から見出し中の社名を探し、同じ会社が出て、かつ話題の語も
重なるイベントを1つにまとめる。外部の形態素解析器は使わない。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from assoc.master.names import CompanyMaster

MIN_KEY_LEN = 3                       # これより短い社名・別名は見出しの検索に使わない(一般語に当たりすぎる)
MIN_SHARED_TOPIC_CHARS = 4            # 同じ会社の2つのイベントを1つにまとめる、共通の話題の語の合計文字数
_HOLDING_TAILS = ("ホールディングス", "ホールディング", "グループ", "hd", "ホールディングズ")
_LEGAL_FORMS = re.compile(r"(株式会社|\(株\)|（株）|㈱|有限会社|合同会社)")
_DROP = re.compile(r"[\s・･\.\-‐－_]")        # 長音「ー」は残す(社名の照合 normalize_name は落とすが、残さないと『ランドー』が『ランド』に当たる)
_KANA = "ァ-ヶー"
_ASCII_HEAD = re.compile(r"^[a-z]{3,}")
_TOPIC_RUN = re.compile(r"[一-龥]{2,}|[ァ-ヶ]{2,}")
# どの会社の話にも出て、話題の一致の根拠にならない語
_GENERIC_TOPIC = frozenset({
    "発表", "開始", "決定", "実施", "影響", "対応", "提供", "予定", "今後", "公表", "見通し", "報告", "状況", "について",
    "関する", "お知らせ", "株式", "会社", "企業", "最大", "全国", "全面", "新た", "初の", "再び", "続く", "相次ぐ",
    "大量保有報告書", "変更報告書", "保有割合", "提出", "ニュース", "速報",   # 別々の会社の定型の報告書・媒体の見出し語
})
# 配信元の表記(「…（テレビ朝日系（ANN））」「… | 全国のニュース」)。社名の照合から外す
_MEDIA_TAIL = re.compile(r"[（(][^（()）]*系[（(][^（()）]*[）)]\s*[）)]|[（(][^（()）]*系[）)]")


def _strip_media(title: str) -> str:
    from assoc.events.digest import clean_title
    return clean_title(_MEDIA_TAIL.sub("", title or ""))


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    return _DROP.sub("", _LEGAL_FORMS.sub("", s)).lower()


@dataclass(frozen=True)
class Mention:
    key: str                          # 見出しに出た社名(正規化後)。同じ会社の話をまとめる鍵
    codes: tuple[str, ...]            # 該当する銘柄コード(ブランド名だけのときはそのグループ全社)
    group: bool = False               # True ならブランド名(GMO など)だけで、特定の1社ではない


class CompanyIndex:
    """見出し中の社名を探す。長い社名を先に当て、英字の社名は前後が英数字でないときだけ当てる。"""

    def __init__(self, key_codes: dict[str, set[str]], group_keys: set[str]):
        self._codes = key_codes
        self._groups = group_keys
        self._lens = sorted({len(k) for k in key_codes}, reverse=True)

    @staticmethod
    def _bounded(text: str, i: int, j: int) -> bool:
        """text[i:j] の前後が、同じ種類の文字(英数字どうし・カタカナどうし)でつながっていないか。"""
        k = text[i:j]
        if i > 0:
            a, f = text[i - 1], k[0]
            if (f.isascii() and f.isalnum() and a.isascii() and a.isalnum()) or (
                    "ァ" <= f <= "ヿ" and "ァ" <= a <= "ヿ"):
                return False
        if j < len(text):
            a, f = text[j], k[-1]
            if (f.isascii() and f.isalnum() and a.isascii() and a.isalnum()) or (
                    "ァ" <= f <= "ヿ" and "ァ" <= a <= "ヿ"):
                return False
        return True

    @classmethod
    def from_master(cls, master: CompanyMaster, aliases: dict[str, str] | None = None,
                    stop: set[str] | None = None) -> "CompanyIndex":
        stop = {normalize(w) for w in (stop or set())}
        key_codes: dict[str, set[str]] = {}
        full: dict[str, set[str]] = {}
        for code, name in master.names.items():
            n = normalize(name)
            if len(n) >= MIN_KEY_LEN and n not in stop:
                full.setdefault(n, set()).add(code)
        key_codes.update({k: set(v) for k, v in full.items()})
        # 「…ホールディングス」「…グループ」を外した短い名前(見出しでは省かれる)。正式名と衝突するものは使わない
        for n, codes in full.items():
            for tail in _HOLDING_TAILS:
                if n.endswith(tail) and len(n) - len(tail) >= MIN_KEY_LEN:
                    short = n[: -len(tail)]
                    if short not in full:
                        key_codes.setdefault(short, set()).update(codes)
        # 英字で始まる名前の先頭(GMO・SBI など)が2社以上に共通なら、ブランド名としてグループ全社に結ぶ
        brands: dict[str, set[str]] = {}
        for n, codes in full.items():
            m = _ASCII_HEAD.match(n)
            if m:
                brands.setdefault(m.group(0), set()).update(codes)
        groups = {b for b, codes in brands.items() if len(codes) >= 2}
        for b in groups:
            key_codes.setdefault(b, set()).update(brands[b])
        # 別名辞書(ユニクロ→9983 など)。短い別名は一般語に当たるので長さの下限を守る
        for alias, code in (aliases or {}).items():
            a = normalize(alias)
            if len(a) >= MIN_KEY_LEN and a not in stop and code in master.names:
                key_codes.setdefault(a, set()).add(code)
        for w in stop:
            key_codes.pop(w, None)
        return cls(key_codes, groups)

    @classmethod
    def from_db(cls, con, aliases_file: Path | None = None, stop_file: Path | None = None) -> "CompanyIndex":
        master = CompanyMaster.from_db(con)
        aliases, stop = {}, set()
        if aliases_file and Path(aliases_file).exists():
            with Path(aliases_file).open(encoding="utf-8") as f:
                aliases = {str(k): str(v) for k, v in (yaml.safe_load(f) or {}).items()}
        if stop_file and Path(stop_file).exists():
            with Path(stop_file).open(encoding="utf-8") as f:
                stop = {str(w) for w in (yaml.safe_load(f) or [])}
        return cls.from_master(master, aliases, stop)

    def find(self, title: str) -> list[Mention]:
        """見出し中の社名。長い社名から順に当て、すでに当たった範囲に重なるものは捨てる。"""
        text = normalize(_strip_media(title))
        taken = [False] * len(text)
        out, seen = [], set()
        for n in self._lens:
            for i in range(0, len(text) - n + 1):
                k = text[i:i + n]
                if k not in self._codes or any(taken[i:i + n]) or not self._bounded(text, i, i + n):
                    continue
                for x in range(i, i + n):
                    taken[x] = True
                if k not in seen:
                    seen.add(k)
                    out.append(Mention(key=k, codes=tuple(sorted(self._codes[k])), group=k in self._groups))
        return out

    def brand_of(self, key: str) -> str | None:
        """社名の先頭がブランド名(gmo など)ならそのブランド名。"""
        m = _ASCII_HEAD.match(key)
        return m.group(0) if m and m.group(0) in self._groups else None


def story_keys(index: CompanyIndex, title: str) -> set[str]:
    """同じ会社の話をまとめる鍵。社名そのものに加え、その会社のブランド名(GMOインターネットグループ→gmo)を含める。"""
    keys: set[str] = set()
    for mt in index.find(title):
        keys.add(mt.key)
        b = index.brand_of(mt.key)
        if b:
            keys.add(b)
    return keys


def topic_runs(title: str, keys: set[str]) -> set[str]:
    """見出しから社名を除いた、話題の語(漢字・カタカナの連続)。"""
    text = normalize(_strip_media(title))
    for k in sorted(keys, key=len, reverse=True):
        text = text.replace(k, " ")
    return {r for r in _TOPIC_RUN.findall(text) if r not in _GENERIC_TOPIC}


def same_topic(a: set[str], b: set[str]) -> bool:
    shared = a & b
    return sum(len(r) for r in shared) >= MIN_SHARED_TOPIC_CHARS
