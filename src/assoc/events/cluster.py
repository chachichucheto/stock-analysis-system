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

from assoc.events.companies import CompanyIndex, same_topic, story_keys, topic_runs
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
        self.indexed_bg: set[str] = set()   # 転置索引に登録済みの要素(cluster() が使う)
        self.indexed_en: set[str] = set()

    def add(self, item: ClusterInput) -> None:
        self.members.append(item)
        self.title_bigrams |= char_bigrams(item.title)
        self.entities |= set(item.entities)

    def absorb(self, other: "_Cluster") -> None:
        self.members.extend(other.members)
        self.title_bigrams |= other.title_bigrams
        self.entities |= other.entities

    def similarity(self, item: ClusterInput) -> float:
        title_sim = jaccard(self.title_bigrams, char_bigrams(item.title))
        entity_sim = jaccard(self.entities, set(item.entities))
        return max(title_sim, entity_sim)


def _prefix_keys(keys: set[str], postings: dict[str, list[int]]) -> list[str]:
    """類似度(Jaccard)が 0.5 以上になるクラスタは、この集合の要素の半分以上を共有する。したがって、共有数の少ない順(珍しい要素)に
    len - ceil(len/2) + 1 個だけ見れば、該当するクラスタを必ず1つは拾える(鳩の巣原理)。珍しい要素を先に使い、候補を絞る。"""
    n = len(keys)
    need = n - (n + 1) // 2 + 1
    return sorted(keys, key=lambda k: len(postings.get(k, ())))[:need]


MAX_STORY_GROUP = 400   # 1つの社名に結びつくクラスタがこれを超えたら、総当たりの比較を打ち切る(処理時間の上限)


def _merge_same_company(clusters: list[_Cluster], index: CompanyIndex) -> tuple[list[_Cluster], dict[int, set[str]]]:
    """同じ会社の名前が出て、話題の語も重なるクラスタを1つにまとめる(D48)。

    見出しの言い回しが割れると、1つの話が媒体数1〜2のイベントに砕ける。社名と話題が一致するものは同じ話とみなし、
    記事と媒体を合算する。社名が同じでも話題が違えば(決算と不正アクセスなど)まとめない。"""
    keys: list[set[str]] = []
    topics: list[set[str]] = []
    for c in clusters:
        ks: set[str] = set()
        for m in c.members:
            ks |= story_keys(index, m.title)
        keys.append(ks)
        tp: set[str] = set()
        for m in c.members:
            tp |= topic_runs(m.title, ks)
        topics.append(tp)
    parent = list(range(len(clusters)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_key: dict[str, list[int]] = {}
    for i, ks in enumerate(keys):
        for k in ks:
            by_key.setdefault(k, []).append(i)
    for members in by_key.values():
        members = members[:MAX_STORY_GROUP]
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                if find(i) != find(j) and same_topic(topics[i], topics[j]):
                    parent[find(j)] = find(i)
    merged: dict[int, _Cluster] = {}
    merged_keys: dict[int, set[str]] = {}
    for i, c in enumerate(clusters):
        r = find(i)
        if r in merged:
            merged[r].absorb(c)
            merged_keys[r] |= keys[i]
        else:
            merged[r] = c
            merged_keys[r] = set(keys[i])
    out = list(merged.values())
    return out, {n: merged_keys[r] for n, r in enumerate(merged)}


def cluster(items: list[ClusterInput], *, threshold: float = 0.5,
            company_index: CompanyIndex | None = None) -> list[Event]:
    """見出し・固有表現の類似度でイベントにまとめる(純関数)。

    items は同じ日にまとめて渡す想定(1日単位でのイベント化。docs/DESIGN.md §6.2)。

    全クラスタと総当たりで比べると、数日分(数千件)で数分かかる(2026-10-17:6,865件で224秒。D43)。
    しきい値以上になるクラスタは、見出しの文字2-gram か固有表現の半分以上を共有するので、転置索引で候補を絞ってから
    厳密に比べる。結果は総当たりと同じ(同点は先に作ったクラスタが勝つ)。threshold が 0.5 未満のときは絞れないので総当たりに戻す。
    """
    clusters: list[_Cluster] = []
    bg_post: dict[str, list[int]] = {}
    en_post: dict[str, list[int]] = {}
    prune = threshold >= 0.5

    def register(ci: int, c: _Cluster, bg: set[str], ents: set[str]) -> None:
        for k in bg - c.indexed_bg:
            bg_post.setdefault(k, []).append(ci)
        for k in ents - c.indexed_en:
            en_post.setdefault(k, []).append(ci)
        c.indexed_bg |= bg
        c.indexed_en |= ents

    for item in items:
        if not item.entities:
            item.entities = list(extract_entities(item.title))
        bg, ents = char_bigrams(item.title), set(item.entities)
        if prune:
            cand: set[int] = set()
            for k in _prefix_keys(bg, bg_post):
                cand.update(bg_post.get(k, ()))
            for k in _prefix_keys(ents, en_post):
                cand.update(en_post.get(k, ()))
            order = sorted(cand)
        else:
            order = range(len(clusters))
        best, best_score = None, 0.0
        for ci in order:
            c = clusters[ci]
            score = max(jaccard(c.title_bigrams, bg), jaccard(c.entities, ents))
            if score > best_score:
                best, best_score, best_i = c, score, ci
        if best is not None and best_score >= threshold:
            best.add(item)
            register(best_i, best, best.title_bigrams, best.entities)
        else:
            c = _Cluster(item)
            clusters.append(c)
            register(len(clusters) - 1, c, c.title_bigrams, c.entities)

    story: dict[int, set[str]] = {}
    if company_index is not None:
        clusters, story = _merge_same_company(clusters, company_index)

    events = []
    for n, c in enumerate(clusters):
        # 代表見出しは、最も長いタイトル(要点が詳しいことが多いため)。
        rep = max(c.members, key=lambda m: len(m.title))
        media_count = len({m.source for m in c.members})
        codes = sorted({m.code for m in c.members if m.code})
        disclosure_kinds = [m for m in c.members if m.kind == "disclosure"]
        first_observed_at = min(m.first_observed_at for m in c.members)
        skeys = sorted(story.get(n, ()))
        mcodes = sorted({code for k in skeys for code in company_index._codes.get(k, ())}) if skeys else []
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
                story_keys=skeys,
                mentioned_codes=mcodes,
            )
        )
    return events


def media_of(source: str, summary: str = "") -> str:
    """媒体数(ニュースの大きさ)を数えるための「媒体名」を返す。

    - RSS は「媒体名 カテゴリ」の形の名前(例 `NHK 国際` `朝日新聞 ビジネス`)なので、先頭の語が媒体。
      同じ媒体の複数フィード(NHK の4本)を、別々の媒体と数えない。
    - Google News の記事は source が検索語(`google_news:半導体`)で、実際の媒体名は要約欄にある。
      検索語を媒体として数えると、同じ記事が複数の検索に当たっただけで水増しされる。
    """
    if source.startswith("google_news:"):
        return summary.strip() or "google_news"
    return source.split()[0] if source.split() else source


def build_events_for_day(con, as_of: date, *, threshold: float = 0.5,
                         company_index: CompanyIndex | None = None) -> list[Event]:
    """DB からその日(日本時間)の news_item・disclosure を読み、イベント化する。"""
    start = datetime.combine(as_of, time.min, tzinfo=JST)
    return build_events_between(con, start, start + timedelta(days=1), threshold=threshold,
                                company_index=company_index)


def build_events_between(con, start: datetime, end: datetime, *, threshold: float = 0.5,
                         company_index: CompanyIndex | None = None) -> list[Event]:
    """start < 取得時刻 <= end の news_item・disclosure を読み、イベント化する。

    夕方の入力パックは「前回のパックを作った時刻から今回まで」を対象にする。日付で区切ると、
    パックを作った後の夜に取得したニュースが、どの日のパックにも入らなくなるため。"""
    news_rows = con.execute(
        """SELECT news_id, title, source, first_observed_at, summary
           FROM news_item WHERE first_observed_at > ? AND first_observed_at <= ?""",
        [start, end],
    ).fetchall()
    disc_rows = con.execute(
        """SELECT disclosure_id, title, source, first_observed_at, code
           FROM disclosure WHERE first_observed_at > ? AND first_observed_at <= ?""",
        [start, end],
    ).fetchall()
    items = [
        ClusterInput(id=r[0], title=r[1] or "", kind="news", source=media_of(r[2] or "", r[4] or ""),
                     first_observed_at=r[3])
        for r in news_rows
    ] + [
        ClusterInput(id=r[0], title=r[1] or "", kind="disclosure", source=r[2] or "",
                     first_observed_at=r[3], code=r[4])
        for r in disc_rows
    ]
    return cluster(items, threshold=threshold, company_index=company_index)
