"""同じ出来事の記事・開示をイベントにまとめる(docs/DESIGN.md §6.2-1)。

外部の形態素解析器は使わず、次の2つを組み合わせた簡易な類似度でクラスタリングする。
1. 見出しの文字2-gram(バイグラム)の Jaccard 係数
2. 見出しから正規表現で抜き出した「固有名詞らしき文字列」(漢字・カタカナの連続)の重なり

しきい値以上の類似度を持つ既存クラスタが見つかればそこに加え、無ければ新しいクラスタを作る
(1パスの貪欲法)。精度は粗いが、Python 標準ライブラリのみで完結し、依存が増えない。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from assoc.events.models import Event
from assoc.ingest.common import normalize_text, sha256_hex
from assoc.timeutil import JST, jst_date, to_iso, utcnow

_ENTITY_RE = re.compile(r"[一-龥ー]{2,}|[ァ-ヶー]{2,}")
_NOISE_CHARS = re.compile(r"[\s、。,\.　「」『』（）()【】\-—―:：/／]+")


@dataclass
class ClusterInput:
    """クラスタリングの対象1件(news_item または disclosure の要点)。"""

    id: str
    title: str
    kind: str  # "news" | "disclosure"
    source: str
    first_observed_at: datetime
    code: str | None = None
    entities: list[str] = field(default_factory=list)


def extract_entities(text: str) -> set[str]:
    """漢字・カタカナの連続を「固有名詞らしき文字列」として抜き出す(簡易ヒューリスティック)。"""
    return set(_ENTITY_RE.findall(text or ""))


def char_bigrams(text: str) -> set[str]:
    t = _NOISE_CHARS.sub("", text or "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _similarity(a: ClusterInput, b: ClusterInput) -> float:
    title_sim = jaccard(char_bigrams(a.title), char_bigrams(b.title))
    entity_sim = jaccard(set(a.entities), set(b.entities))
    return max(title_sim, entity_sim)


def _novelty_hash(title: str, entities: set[str]) -> str:
    """正規化した見出しの要点から作る新規性のハッシュ(docs/DESIGN.md §5)。

    表記ゆれをある程度吸収するため、句読点・空白を除いた文字列と、上位の固有表現
    (辞書順で先頭3つ)を組み合わせる。厳密な同一性ではなく「ほぼ同じ話」を狙う。
    """
    stripped = _NOISE_CHARS.sub("", normalize_text(title))
    top_entities = "|".join(sorted(entities)[:3])
    return sha256_hex(f"{top_entities}::{stripped}")


class _Cluster:
    def __init__(self, first: ClusterInput):
        self.members: list[ClusterInput] = [first]
        self.title_bigrams = char_bigrams(first.title)
        self.entities: set[str] = set(first.entities)

    def add(self, item: ClusterInput) -> None:
        self.members.append(item)
        self.title_bigrams |= char_bigrams(item.title)
        self.entities |= set(item.entities)

    def similarity(self, item: ClusterInput) -> float:
        title_sim = jaccard(self.title_bigrams, char_bigrams(item.title))
        entity_sim = jaccard(self.entities, set(item.entities))
        return max(title_sim, entity_sim)


def cluster(items: list[ClusterInput], *, threshold: float = 0.5) -> list[Event]:
    """見出し・固有表現の類似度でイベントにまとめる(純関数)。

    items は同じ日にまとめて渡す想定(1日単位でのイベント化。docs/DESIGN.md §6.2)。
    """
    clusters: list[_Cluster] = []
    for item in items:
        if not item.entities:
            item.entities = list(extract_entities(item.title))
        best, best_score = None, 0.0
        for c in clusters:
            score = c.similarity(item)
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= threshold:
            best.add(item)
        else:
            clusters.append(_Cluster(item))

    events = []
    for c in clusters:
        # 代表見出しは、最も長いタイトル(要点が詳しいことが多いため)。
        rep = max(c.members, key=lambda m: len(m.title))
        media_count = len({m.source for m in c.members})
        codes = sorted({m.code for m in c.members if m.code})
        disclosure_kinds = [m for m in c.members if m.kind == "disclosure"]
        first_observed_at = min(m.first_observed_at for m in c.members)
        events.append(
            Event(
                event_id=sha256_hex(
                    jst_date(first_observed_at).isoformat() + "::" + _novelty_hash(rep.title, c.entities)
                ),
                title=rep.title,
                news_ids=[m.id for m in c.members if m.kind == "news"],
                entities=sorted(c.entities),
                first_observed_at=first_observed_at,
                media_count=media_count,
                disclosure_type="disclosure" if disclosure_kinds else "",
                novelty_hash=_novelty_hash(rep.title, c.entities),
                codes=codes,
                disclosure_ids=[m.id for m in c.members if m.kind == "disclosure"],
            )
        )
    return events


def build_events_for_day(con, as_of: date, *, threshold: float = 0.5) -> list[Event]:
    """DB からその日(日本時間)の news_item・disclosure を読み、イベント化する。"""
    start = datetime.combine(as_of, time.min, tzinfo=JST)
    return build_events_between(con, start, start + timedelta(days=1), threshold=threshold)


def build_events_between(con, start: datetime, end: datetime, *, threshold: float = 0.5) -> list[Event]:
    """start < 取得時刻 <= end の news_item・disclosure を読み、イベント化する。

    夕方の入力パックは「前回のパックを作った時刻から今回まで」を対象にする。日付で区切ると、
    パックを作った後の夜に取得したニュースが、どの日のパックにも入らなくなるため。"""
    news_rows = con.execute(
        """SELECT news_id, title, source, first_observed_at
           FROM news_item WHERE first_observed_at > ? AND first_observed_at <= ?""",
        [start, end],
    ).fetchall()
    disc_rows = con.execute(
        """SELECT disclosure_id, title, source, first_observed_at, code
           FROM disclosure WHERE first_observed_at > ? AND first_observed_at <= ?""",
        [start, end],
    ).fetchall()
    items = [
        ClusterInput(id=r[0], title=r[1] or "", kind="news", source=r[2] or "",
                     first_observed_at=r[3])
        for r in news_rows
    ] + [
        ClusterInput(id=r[0], title=r[1] or "", kind="disclosure", source=r[2] or "",
                     first_observed_at=r[3], code=r[4])
        for r in disc_rows
    ]
    return cluster(items, threshold=threshold)
