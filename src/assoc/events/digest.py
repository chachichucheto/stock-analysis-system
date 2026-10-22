"""見出し一覧(headline_digest):足切りに関係なく、その日の見出しを重複をまとめて1行ずつ並べる(D47)。

足切りは『媒体数の上位5%・上限3件』で機械的に絞るため、言い回しが割れる大きなニュース(政策・事件・災害・為替・
サイバー攻撃など)が落ちる(10/21: 約1,770件のうち18件しか夜の手順に渡らなかった)。ここでは絞らず、
どれが強いニュースかを LLM が見出しから選べるようにする。トークンを抑えるため、1行は見出しだけ、件数は上限で切る。
"""
from __future__ import annotations

import re

from assoc.events.models import Event
from assoc.ingest.common import normalize_text

DIGEST_LIMIT = 100        # 一覧に載せる行数の上限(1行 約50字。220行だと約1.2万字で、後半は記事数1件の販促・地域の話題ばかりだった)
MIN_ARTICLES = 2          # 記事数がこれ未満の行は載せない(1件だけの報道は、足切りを通ったイベントか急増語で拾う)
SIMILARITY = 0.4          # 見出しの文字2-gramの Jaccard がこれ以上なら同じ話としてまとめる
MAX_TITLE_CHARS = 60

# 末尾の媒体名(「 - NHKニュース」「｜Infoseekニュース」「（時事通信）」「(株探ニュース)」)
_TAIL_RE = re.compile(r"\s*(?:[-－—―|｜]\s*[^-－—―|｜]{2,25}|[（(][^（()）]{2,20}[）)])\s*$")
# 投資の連想に使えない販促・催事・広告の見出し
_NOISE_RE = re.compile(
    r"プレスリリース|PR\s?TIMES|お知らせ|開催レポート|出展|セミナー|ウェビナー|キャンペーン|ランキング|"
    r"無料|プレゼント|発売開始|提供開始|提供を開始|市場分析レポート|市場規模|"
    r"口コミ|おすすめ|レシピ|占い|試合|優勝|大量保有報告書|変更報告書|^外為：|競技会|マイクラ|マインクラフト|グッズ|イラスト|"
    r"来場登録|フォークリフト|AI彼女|最大震度[0-2１２]の地震|震度[0-2１２]の地震|震度[0-2１２]で")
# 日経平均・東証の値動きの見出し(毎日10行以上になる)。1行にまとめる。個別銘柄や政策の話は含めない
_MARKET_RE = re.compile(r"日経平均.*(?:円安|円高|寄り付き|前引け|終値|大引け|反落|反発|続落|続伸|急落)|"
                        r"東証.*(?:\d+円安|\d+円高|終値)|\d+円(?:安|高)の[\d,]*万?[\d,]*円.*平均株価")


def clean_title(title: str) -> str:
    t = normalize_text(title or "")
    for _ in range(2):
        t = _TAIL_RE.sub("", t)
    return t.strip()


def _grams(t: str) -> set[str]:
    s = re.sub(r"[\s、。,.　「」『』（）()【】\-—―:：/／！!？?]+", "", t)
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def _similar(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= SIMILARITY


def build_digest(events: list[Event], exclude_titles: list[str], limit: int = DIGEST_LIMIT) -> list[str]:
    """events(足切り前の全イベント)から、重複をまとめた見出し一覧を作る。

    `exclude_titles` は、すでに `events` に載せた見出し(重複して載せない)。
    戻り値は『d{連番} {記事数}件 {見出し}』の文字列を、記事数の多い順に並べたもの。
    """
    taken = [_grams(clean_title(t)) for t in exclude_titles]
    clusters: list[dict] = []
    market: list[tuple[int, str]] = []
    for e in sorted(events, key=lambda x: x.first_observed_at, reverse=True):
        title = clean_title(e.title)
        if len(title) < 8 or _NOISE_RE.search(title):
            continue
        if _MARKET_RE.search(title):
            market.append((max(e.media_count, 1) + len(e.news_ids) - 1, title))
            continue
        g = _grams(title)
        if any(_similar(g, x) for x in taken):
            continue
        weight = max(e.media_count, 1) + len(e.news_ids) - 1
        for c in clusters:
            if _similar(g, c["g"]):
                c["n"] += weight
                break
        else:
            clusters.append({"g": g, "title": title[:MAX_TITLE_CHARS], "n": weight})
    clusters = [c for c in clusters if c["n"] >= MIN_ARTICLES]
    clusters.sort(key=lambda c: -c["n"])
    lines = [f"{c['n']}件 {c['title']}" for c in clusters[:limit - (1 if market else 0)]]
    if market:
        # 相場の値動きは1行に(代表は報じた数が最も多い見出し)。原因の分析は株価の急変イベントと market で見る
        top = max(market, key=lambda m: m[0])[1]
        lines.insert(0, f"{sum(n for n, _ in market)}件 【相場の値動き】{len(market)}本の見出し。代表: {top[:40]}")
    return [f"d{i} {x}" for i, x in enumerate(lines, 1)]
