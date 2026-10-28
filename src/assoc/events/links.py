"""企業の関係の地図(D50-B)。ある銘柄の材料から、連想の候補になる銘柄を『経路つき』で引く。

連想先を夜の判断だけに任せると、事業上の関係を確認できずに見送りが出る(10/22 のGMOペパボ・グローバルサイン)。
ここでは機械が確かめられる関係だけを集め、経路(なぜ関係するか)を添えて入力パックに渡す。経路の強さの順:
  1. 手で確かめた関係(knowledge/company_links.yaml)
  2. 同じグループ(社名の共通の英字ブランド。GMO・SBI・NTTなど)
  3. 過去の判断で同じシナリオの候補になった(data の candidate 記録)
  4. 同じテーマの代表銘柄(knowledge/theme_stocks.yaml)
  5. 直近のニュースで一緒に出た(2回以上)
関係の有無は株価で決めない(ニュースが先)。経路は推測の起点であり、結論ではない。
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from pathlib import Path

import yaml

from assoc.events.companies import CompanyIndex

STRENGTH = {"curated": 5, "group": 4, "scenario": 3, "theme": 2, "co_mention": 1}
MIN_CO_MENTIONS = 2
PER_KIND = 3               # 連想の候補に載せる、最も強い経路の種類ごとの上限
MAX_THEME_SIZE = 12       # これを超えるテーマは広すぎて、同じテーマだけでは関係の根拠にしない


class LinkMap:
    def __init__(self):
        self._edges: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))

    def add(self, a: str, b: str, kind: str, note: str) -> None:
        if a == b:
            return
        for x, y in ((a, b), (b, a)):
            self._edges[x][y].setdefault(kind, note)

    def __len__(self) -> int:
        return sum(len(v) for v in self._edges.values()) // 2

    def related(self, code: str, *, limit: int = 6) -> list[dict]:
        out = []
        for other, kinds in self._edges.get(code, {}).items():
            score = sum(STRENGTH[k] for k in kinds)
            out.append((score, other, kinds))
        out.sort(key=lambda t: (-t[0], t[1]))
        # 同じ種類の経路(GMOグループ10社など)が上位を占めないよう、最も強い経路の種類ごとに上限を置く
        taken: dict[str, int] = defaultdict(int)
        picked = []
        for score, o, kinds in out:
            top = max(kinds, key=lambda k: STRENGTH[k])
            if taken[top] >= PER_KIND:
                continue
            taken[top] += 1
            picked.append((o, kinds))
        return [{"code": o, "via": [note for _k, note in sorted(k.items(), key=lambda kv: -STRENGTH[kv[0]])]}
                for o, k in picked[:limit]]


def add_groups(links: LinkMap, index: CompanyIndex, names: dict[str, str]) -> None:
    for brand in index.group_brands():
        members = sorted(index.codes_of(brand))
        if len(members) > 40:
            continue
        for a, b in itertools.combinations(members, 2):
            links.add(a, b, "group", f"同じグループ({brand.upper()})")


def add_curated(links: LinkMap, path: Path) -> None:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for row in (yaml.safe_load(f) or []):
            a, b = str(row.get("a", "")), str(row.get("b", ""))
            if a and b:
                links.add(a, b, "curated", f"{row.get('kind', '関係')}:{row.get('note', '')}".rstrip(":"))


def add_scenarios(links: LinkMap, candidate_rows) -> None:
    """過去の判断で、同じシナリオの候補に並んだ銘柄どうし。"""
    by_sc: dict[str, set[str]] = defaultdict(set)
    for r in candidate_rows:
        if r.get("scenario_id") and r.get("code"):
            by_sc[r["scenario_id"]].add(str(r["code"]))
    for sc, codes in by_sc.items():
        if len(codes) > MAX_THEME_SIZE:
            continue
        for a, b in itertools.combinations(sorted(codes), 2):
            links.add(a, b, "scenario", f"過去のシナリオ{sc}で並んだ")


def add_themes(links: LinkMap, theme_stocks: dict[str, list[str]], theme_names: dict[str, str]) -> None:
    for tid, codes in theme_stocks.items():
        codes = sorted({str(c) for c in codes})
        if len(codes) > MAX_THEME_SIZE:
            continue
        for a, b in itertools.combinations(codes, 2):
            links.add(a, b, "theme", f"同じテーマ({theme_names.get(tid, tid)})")


def add_co_mentions(links: LinkMap, index: CompanyIndex, texts, *, min_count: int = MIN_CO_MENTIONS) -> None:
    pair: dict[tuple[str, str], int] = defaultdict(int)
    for t in texts:
        cs = sorted({c[0] for m in index.find(t) if len(c := index.subject_codes(m.key)) == 1})
        for a, b in itertools.combinations(cs, 2):
            pair[(a, b)] += 1
    for (a, b), n in pair.items():
        if n >= min_count:
            links.add(a, b, "co_mention", f"直近のニュースで{n}回一緒に出た")
