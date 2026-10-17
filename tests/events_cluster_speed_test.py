"""クラスタリングの転置索引が、総当たりと同じ結果を返すこと(events/cluster.py。D43)。"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from assoc.events.cluster import ClusterInput, _Cluster, cluster, extract_entities


def _naive(items, threshold=0.5):
    clusters = []
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
    return [sorted(m.id for m in c.members) for c in clusters]


def _items(seed, n):
    rnd = random.Random(seed)
    words = ["半導体", "日経平均", "ソースネクスト", "情報漏えい", "トヨタ", "決算", "上方修正", "米CPI", "利上げ", "急落",
             "東京エレクトロン", "TOB", "公開買付", "アドバンテスト", "円安", "データセンター", "サイバー", "不正アクセス"]
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        title = "".join(rnd.choice(words) + rnd.choice(["、", "が", "の", "を", ""]) for _ in range(rnd.randint(2, 5)))
        out.append(ClusterInput(id=f"i{i}", title=title, kind="news", source=f"s{rnd.randint(0, 5)}", first_observed_at=now))
    return out


def test_indexed_clustering_matches_naive():
    for seed in range(3):
        a = _items(seed, 400)
        b = _items(seed, 400)
        got = [sorted(n for n in e.news_ids) for e in cluster(a)]
        want = _naive(b)
        assert got == want
