"""個別銘柄を狙い撃つ材料の検知(D49)。

足切り(gate.py)は報道の量で通すかを決めるので、報道は少なくても、特定の上場企業の株価が確実に動く材料
(自社の不正アクセス、業績予想の修正、まとまった受注、TOB、上場廃止の懸念など)を落とす。
ここでは量とは別に、次の4つで通す:
  1. 誰の話か   :見出しの社名(D48の銘柄特定)か、開示の発行会社。特定の1社であること
  2. 何が起きたか:材料の種類(下の表。重みは 2026-09 の開示258件の翌営業日の反応から付けた)
  3. 確かさ     :本人の開示、複数の媒体、金額の記載
  4. 動けるか   :20日平均の売買代金が下限(3億円。D33)以上
「大したことのない個別ニュース」は、種類が弱い・確かさが無い・流動性が無いのいずれかで落ちる。
株価の動きは検知に使わない(ニュースが先)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from assoc.events.companies import CompanyIndex
from assoc.events.models import Event

PASS_SCORE = 3          # 種類の重み+確かさ の合計がこれ以上で通す
MAX_PER_DAY = 8         # 1日に狙い撃ちで通す上限(LLM に渡す量の歯止め)
REPEAT_DAYS = 3         # 同じ銘柄・同じ種類は、通してからこの日数は再び通さない

# (種類, 見出しの正規表現, 重み)。上にあるものが優先。重みは翌営業日にTOPIX超過±5%以上動いた割合(市場全体は6.9%)で付けた:
# 業績修正33%・受注/契約56%・配当修正33%・増資等28%・提携25%(この3つ以下は件数が少ない)。M&Aは8%・承認/治験は0%で、市場平均以下。
# サイバー事故・上場廃止等は4件ずつで統計にならないため、GMOの実例と一般の理解から暫定で重く置く
_TYPES: list[tuple[str, re.Pattern, int]] = [(label, re.compile(pat), w) for label, pat, w in [
    ("上場廃止等", r"上場廃止|監理銘柄|整理銘柄|債務超過|継続企業の前提|民事再生|会社更生|破産", 3),
    # 政治・地政学のリスク(D52)。2026-11 にロシアが JT の資産を接収しても、見出しにも材料の種類にも無く、拾えなかった。
    # 接収・国有化・資産凍結は事業そのものを失うので重く、制裁・輸出規制・関税は広く使われる語なので、報道の確かさと合わせて通す
    ("接収・国有化", r"接収|没収|国有化|資産凍結|資産を凍結|暫定管理|操業停止|営業許可.{0,3}取り消|事業停止命令", 3),
    ("制裁・規制", r"制裁|輸出規制|輸出管理|禁輸|輸入禁止|関税|規制強化|規制案|販売禁止|使用禁止", 2),
    ("TOB", r"公開買付|TOB|ＴＯＢ", 3),
    ("業績修正", r"業績予想の修正|業績予想を修正|上方修正|下方修正|業績予想及び|通期業績予想|特別損失|減損", 3),
    ("事故・不祥事", r"不正アクセス|サイバー攻撃|ランサム|情報漏えい|情報漏洩|システム障害|不適切会計|不正会計|"
                     r"特別調査委員会|第三者委員会|課徴金|行政処分|業務停止|業務改善命令|リコール|工場.{0,3}(?:火災|爆発)", 3),
    ("受注・契約", r"受注|契約締結|落札|採択", 2),
    ("配当・還元", r"増配|復配|配当予想の修正|株式分割|自己株式の取得に係る事項|自社株買い", 2),
    ("提携", r"資本業務提携|資本提携|業務提携|基本合意", 2),
    ("増資・希薄化", r"公募増資|第三者割当|新株予約権付社債|転換社債|ユーロ円CB|新株式発行", 2),
    ("暗号資産", r"暗号資産|ビットコイン|イーサリアム|ステーブルコイン", 2),
    ("承認・治験", r"承認取得|承認を取得|治験|薬事承認|FDA|臨床試験", 2),
    ("M&A", r"株式交換|合併|経営統合|株式移転|子会社化|事業譲渡|吸収分割", 1),
]]
# 定型・販促・予定された開示。種類に当たっても通さない(公開買付・修正の語を含む開示は除く)
_NOISE = re.compile(r"説明資料|質疑応答|補足説明|日々の開示|運用状況|主要株主の異動|ストックオプション|取得状況|"
                    r"コーポレート・ガバナンス|訂正|株主総会|人事|異動に関するお知らせ|決算短信|セミナー|キャンペーン|大量保有報告書|変更報告書")
_OVERRIDE = re.compile(r"公開買付|修正|不正|攻撃|漏えい|漏洩|障害|廃止|債務超過")   # 定型の題名でも、これを含めば材料
_STALE = re.compile(r"[（(](20\d\d)年\d{1,2}月\d{1,2}日掲載[）)]")   # 「…（2024年2月5日掲載）」のような過去記事の再掲
_AMOUNT = re.compile(r"\d[\d,\.]*\s*(?:億|兆)円|\d+\s*(?:億|兆)")
_MAJOR_CORROBORATION = 5   # 媒体数がこれ以上なら、確かさを2点にする


@dataclass
class SniperHit:
    event_id: str
    code: str
    company_key: str
    type: str
    score: int
    reasons: list[str] = field(default_factory=list)
    title: str = ""
    media_count: int = 0
    mentioned_codes: list[str] = field(default_factory=list)   # 見出しに出た会社すべて(TOBの買い手と売り手など。主役の取り違えを LLM が直せるように)


def classify(title: str) -> tuple[str, int] | None:
    """見出し・開示の題名から材料の種類と重みを返す。種類に当たらない・定型のものは None。"""
    t = title or ""
    for label, pat, w in _TYPES:
        if pat.search(t):
            if _NOISE.search(t) and not _OVERRIDE.search(t):
                return None
            return label, w
    return None


def liquid_codes(cfg, min_turnover: float, *, window: int = 20) -> set[str] | None:
    """20日平均の売買代金が下限以上の銘柄コード。株価ファイルが無ければ None(流動性で絞らない)。"""
    section = (cfg.section("prices").get("layered") or {}) if hasattr(cfg, "section") else {}
    path = section.get("history")
    if not path or not Path(path).exists():
        return None
    import duckdb
    con = duckdb.connect()
    try:
        rows = con.execute(
            """WITH last AS (SELECT max(date) AS d FROM read_parquet(?)),
                    t AS (SELECT code, close * volume AS v,
                                 row_number() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                          FROM read_parquet(?), last WHERE date >= last.d - INTERVAL 60 DAY)
               SELECT code FROM t WHERE rn <= ? GROUP BY code HAVING avg(v) >= ?""",
            [str(path), str(path), window, min_turnover]).fetchall()
    finally:
        con.close()
    return {str(r[0]).replace(".T", "") for r in rows}


def _subjects(ev: Event, index: CompanyIndex) -> list[tuple[str, str]]:
    """イベントの主役の会社 [(社名キー, 銘柄コード)]。開示は発行会社、ニュースは代表見出しに出た社名の特定の1社。"""
    out: list[tuple[str, str]] = []
    if ev.disclosure_type and ev.codes:
        out += [("", c) for c in ev.codes]
    for m in index.find(ev.title):
        codes = index.subject_codes(m.key)
        if len(codes) == 1:                       # 同名・グループ全社に広がるものは、主役の1社を特定できない
            out.append((m.key, codes[0]))
    seen, uniq = set(), []
    for k, c in out:
        if c not in seen:
            seen.add(c)
            uniq.append((k, c))
    return uniq


def detect_sniper(events: list[Event], index: CompanyIndex, *, liquid: set[str] | None,
                  skip_event_ids: set[str] = frozenset(), recent_keys: set[str] = frozenset(),
                  limit: int = MAX_PER_DAY) -> list[SniperHit]:
    """個別銘柄を狙い撃つ材料を探す。skip_event_ids は通常の足切りですでに通ったイベント、
    recent_keys は直近に通した『銘柄:種類』(重ねて出さない)。"""
    hits: list[SniperHit] = []
    for ev in events:
        if ev.event_id in skip_event_ids:
            continue
        cls = classify(ev.title)
        if not cls:
            continue
        stale = _STALE.search(ev.title)
        if stale and int(stale.group(1)) < ev.first_observed_at.year:
            continue
        label, weight = cls
        for key, code in _subjects(ev, index)[:1]:
            if liquid is not None and code not in liquid:
                continue
            if f"{code}:{label}" in recent_keys:
                continue
            score, reasons = weight, [f"種類 {label}(重み{weight})"]
            if ev.disclosure_type and code in ev.codes:
                score += 1
                reasons.append("本人の開示あり")
            if ev.media_count >= _MAJOR_CORROBORATION:
                score += 2
                reasons.append(f"{ev.media_count}媒体が報道")
            elif ev.media_count >= 2:
                score += 1
                reasons.append(f"{ev.media_count}媒体が報道")
            if _AMOUNT.search(ev.title):
                score += 1
                reasons.append("金額の記載")
            if score >= PASS_SCORE:
                others = [c for _k, c in _subjects(ev, index)]
                hits.append(SniperHit(event_id=ev.event_id, code=code, company_key=key, type=label, score=score,
                                      reasons=reasons, title=ev.title, media_count=ev.media_count,
                                      mentioned_codes=others))
    # 同じ銘柄・種類は最も確かなもの1件
    best: dict[str, SniperHit] = {}
    for h in sorted(hits, key=lambda h: (-h.score, -h.media_count)):
        best.setdefault(f"{h.code}:{h.type}", h)
    return sorted(best.values(), key=lambda h: (-h.score, -h.media_count))[:limit]


def sniper_key(h: SniperHit) -> str:
    return f"{h.code}:{h.type}"
