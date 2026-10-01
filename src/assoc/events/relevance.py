"""ニュースの「投資との関係の深さ」(足切りの並べ替えに使う。docs/DECISIONS.md D36)。

媒体数だけで選ぶと、日銀の議事要旨・警察の捜索・会見の謝罪のような、株の連想に使いにくい記事が上位になる
(2026-10-01 予行演習で確認)。見出しが次のどれに当たるかを数える(0〜3)。
  (1) 上場企業の社名を含む
  (2) テーマの語(THEMES.md の「含む範囲」の語)を含む
  (3) 株価に効く事象の語(TOB・提携・上方修正・規制・承認 など)を含む
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from assoc.master.names import normalize_name

DEAL_WORDS = (
    "TOB", "公開買付", "買収", "MBO", "提携", "統合", "上方修正", "下方修正", "増配", "自社株買い", "増産", "減産",
    "承認", "認可", "解禁", "規制", "制裁", "関税", "補助金", "基金", "予算", "禁輸", "輸出規制", "撤退", "受注",
    "最高益", "赤字", "不正", "リコール", "運転再開", "再稼働", "ストップ高", "新規上場", "IPO",
)
# 社名として短すぎる・一般語と衝突するものは使わない
_MIN_NAME_LEN = 3
_NAME_STOP = {"日本", "東京", "大阪", "世界", "未来", "テクノロジー", "ホールディングス", "グループ", "ジャパン"}
_SPLIT = re.compile(r"[、,・/／()（）\s]+")


def theme_words(themes_md: Path) -> set[str]:
    """THEMES.md の表の語(「含む範囲」「補足」の列と、テーマ名)を、3文字以上で拾う。"""
    words: set[str] = set()
    for line in themes_md.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\| [PFMGE]\d{2} \|", line):
            continue
        for cell in line.split("|")[2:4]:
            for w in _SPLIT.split(cell.replace("*", "")):
                if len(w) >= 3 and not w.startswith("20"):
                    words.add(w)
    return words


def build_matcher(master_names: dict[str, str], themes_md: Path) -> Callable[[str], int]:
    names = {normalize_name(n) for n in master_names.values()}
    names = {n for n in names if len(n) >= _MIN_NAME_LEN and n not in _NAME_STOP}
    themes = theme_words(themes_md)
    lengths = sorted({len(n) for n in names})

    def score(title: str) -> int:
        norm = normalize_name(title)
        hit_company = any(norm[i:i + k] in names for k in lengths for i in range(len(norm) - k + 1))
        hit_theme = any(w in title for w in themes)
        hit_deal = any(w in title for w in DEAL_WORDS)
        return int(hit_company) + int(hit_theme) + int(hit_deal)

    return score
