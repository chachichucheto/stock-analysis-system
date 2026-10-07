"""信用残(JPX「日々公表銘柄等信用取引残高」)の取得・保存・需給指標の算出。

想定している形式
----------------
- 公表ページ: `https://www.jpx.co.jp/markets/statistics-equities/margin/index.html`
  ここに最新営業日の `YYYYMMDD_mtdaily.xlsx` へのリンクがある。ファイルの置き場(`tvdivq...-att`)は
  変わりうるので、固定 URL を置かず、ページの中のリンクから探す。
- シートは1枚。1行目から注記が続き、`申込み現在` の行の左(B列)に基準日(例 `2026/10/6`)がある。
- 銘柄の行は A列=単位株数の記号、B列=規制の印(規/日/監…)、C列=貸株の印(株/喚)、D列=銘柄名、E列=市場、
  F列=制/貸/他、G列=コード(5桁。末尾0を除いた4桁が証券コード。新形式は `283A0`)、H列=ISIN、
  I〜K列=売残高・前日比・上場比、L〜N列=買残高・前日比・上場比、O列=取組比率(売残/買残 %)、
  P〜W列=売残(一般・制度)、買残(一般・制度)とそれぞれの前日比。単位は1株。
- 載るのは**日々公表銘柄**(規制・貸株注意などの対象。約400銘柄)と ETF など。全銘柄ではない。
  銘柄が対象から外れた日は、その銘柄の行が無い(残高ゼロではない)。

ローカルで最初に確認すべき点
----------------------------
- 過去のファイルはリンクから消える(直近のみ)。履歴は毎日取り込んで DB に貯める必要がある。
- 銘柄別の全銘柄の信用残は週1回の別資料(銘柄別信用取引週末残高)で、本モジュールの対象外。

解析部分(parse_*・build_metrics・render_report)は純関数。取得(fetch_margin)だけが外部に接続する。
"""
from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd

from assoc.ingest.common import RateLimiter, log_fetch
from assoc.timeutil import jst_date, to_iso, utcnow

INDEX_URL = "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html"

_ISIN = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")
_DAILY_LINK = re.compile(r'href="([^"]*?(\d{8})_mtdaily\.xlsx)"')

# 需給の見立ての基準(初期値。運用で見直したら docs/DECISIONS.md に残す)
BUY_SURGE = 0.10        # 買残が前日比 +10% 以上
BUY_DROP = -0.10        # 買残が前日比 -10% 以下
SELL_SURGE = 0.20       # 売残が前日比 +20% 以上
MIN_LISTED_PCT = 0.5    # 残高が上場株数の0.5%未満の側は、増減率が大きくてもノイズとして見立て・順位から外す
HEAVY_BUY_LISTED = 10.0  # 買残が上場株数の10%以上
HEAVY_RATIO = 5.0       # 信用倍率5倍以上
SELL_HEAVY_RATIO = 1.0  # 信用倍率1倍未満(売り長)
LOOKBACK_DAYS = 5       # 「N営業日前との比較」のN(DB にある日付で数える)

COLUMNS = ["date", "code", "name", "flags", "market", "loan_type",
           "sell_bal", "sell_chg", "sell_listed_pct", "buy_bal", "buy_chg", "buy_listed_pct",
           "sell_general", "sell_system", "buy_general", "buy_system"]


def _num(v) -> float | None:
    """セルの値を数にする。空・`*`(ETF の上場比)は None。`▲` は減少(負)。"""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("　", "").strip()
    if s in ("", "*", "-", "－"):
        return None
    neg = s.startswith(("▲", "△", "-"))
    s = s.lstrip("▲△-+")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def normalize_code(v) -> str:
    """5桁コード(`44400`・`283A0`)を4桁の証券コードにする。"""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s[:4] if len(s) == 5 and s.endswith("0") else s


def _text(v) -> str:
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v).strip()


def parse_daily_excel(raw: bytes | str | Path) -> pd.DataFrame:
    """mtdaily.xlsx を、列が COLUMNS の DataFrame にする(純関数)。

    raw はダウンロードしたバイト列、またはローカルのパス。基準日や銘柄行が見つからなければ ValueError。
    """
    buf = io.BytesIO(raw) if isinstance(raw, (bytes, bytearray)) else raw
    sheet = pd.read_excel(buf, header=None, engine="openpyxl")
    as_of = None
    for _, row in sheet.iterrows():
        if "申込み現在" in _text(row.get(3)):
            as_of = pd.to_datetime(_text(row.get(1)), errors="coerce")
            break
    if as_of is None or pd.isna(as_of):
        raise ValueError("信用残ファイルに基準日(申込み現在)が見つかりません。JPX の形式が変わった可能性があります")
    rows = []
    for _, r in sheet.iterrows():
        if not _ISIN.match(_text(r.get(7))):
            continue
        rows.append({
            "date": as_of.date(),
            "code": normalize_code(r.get(6)),
            "name": _text(r.get(3)).replace("　", " "),
            "flags": "".join(x for x in (_text(r.get(1)), _text(r.get(2))) if x),
            "market": _text(r.get(4)),
            "loan_type": _text(r.get(5)),
            "sell_bal": _num(r.get(8)), "sell_chg": _num(r.get(9)), "sell_listed_pct": _num(r.get(10)),
            "buy_bal": _num(r.get(11)), "buy_chg": _num(r.get(12)), "buy_listed_pct": _num(r.get(13)),
            "sell_general": _num(r.get(15)), "sell_system": _num(r.get(17)),
            "buy_general": _num(r.get(19)), "buy_system": _num(r.get(21)),
        })
    if not rows:
        raise ValueError("信用残ファイルに銘柄の行が見つかりません。JPX の形式が変わった可能性があります")
    return pd.DataFrame(rows, columns=COLUMNS)


def find_daily_url(html: str, base: str = INDEX_URL) -> str:
    """公表ページの HTML から、最新日の mtdaily.xlsx の URL を探す。"""
    links = {m.group(2): m.group(1) for m in _DAILY_LINK.finditer(html)}
    if not links:
        raise ValueError("公表ページに mtdaily.xlsx へのリンクが見つかりません")
    return urljoin(base, links[max(links)])


def upsert_margin(con, df: pd.DataFrame, first_observed_at=None) -> int:
    """margin_daily へ書く。同じ (date, code) は値を更新する。"""
    first_observed_at = to_iso(first_observed_at or utcnow())
    for r in df.to_dict("records"):
        con.execute(
            """INSERT INTO margin_daily
               (date, code, name, flags, market, loan_type, sell_bal, sell_chg, sell_listed_pct,
                buy_bal, buy_chg, buy_listed_pct, sell_general, sell_system, buy_general, buy_system,
                first_observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (date, code) DO UPDATE SET
                 name = excluded.name, flags = excluded.flags, market = excluded.market,
                 loan_type = excluded.loan_type, sell_bal = excluded.sell_bal, sell_chg = excluded.sell_chg,
                 sell_listed_pct = excluded.sell_listed_pct, buy_bal = excluded.buy_bal,
                 buy_chg = excluded.buy_chg, buy_listed_pct = excluded.buy_listed_pct,
                 sell_general = excluded.sell_general, sell_system = excluded.sell_system,
                 buy_general = excluded.buy_general, buy_system = excluded.buy_system""",
            [*(r[c] if not _isnull(r[c]) else None for c in COLUMNS), first_observed_at],
        )
    return len(df)


def _isnull(v) -> bool:
    return v is None or (not isinstance(v, str) and pd.isna(v))


def fetch_margin(cfg, con, url: str | None = None) -> int:
    """最新日の信用残を取得して margin_daily に保存する。保存した銘柄数を返す。"""
    collect = cfg.section("collect")
    limiter = RateLimiter(float(collect.get("request_interval_sec", 1.0)),
                          collect.get("user_agent", "assoc-research/0.1"))
    if url is None:
        index = collect.get("jpx_margin_index_url") or INDEX_URL
        url = find_daily_url(limiter.get(index).text, index)
    df = parse_daily_excel(limiter.get(url, timeout=60).content)
    n = upsert_margin(con, df)
    log_fetch(con, "jpx_margin_asof", True, n, str(df["date"].iloc[0]))
    return n


# ---- 解析 -------------------------------------------------------------------

def load_history(con, as_of: date | None = None, days: int = 60) -> pd.DataFrame:
    """as_of 以前の margin_daily を、新しい日から数えて `days` 日分(日付の数)取り出す。"""
    end = as_of or con.execute("SELECT max(date) FROM margin_daily").fetchone()[0]
    if end is None:
        return pd.DataFrame(columns=COLUMNS)
    df = con.execute(
        f"""SELECT {', '.join(COLUMNS)} FROM margin_daily
            WHERE date <= ? AND date IN (SELECT DISTINCT date FROM margin_daily WHERE date <= ?
                                         ORDER BY date DESC LIMIT ?)
            ORDER BY date, code""", [end, end, days]).df()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def load_avg_volume(con, as_of: date, window: int = 20, min_days: int = 10) -> dict[str, float]:
    """price_copy から、銘柄ごとの直近 window 日の平均出来高を求める(データ不足の銘柄は含めない)。"""
    rows = con.execute(
        """SELECT code, avg(volume) FROM (
               SELECT code, volume, row_number() OVER (PARTITION BY code ORDER BY date DESC) AS rn
               FROM price_copy WHERE date <= ? AND volume IS NOT NULL)
           WHERE rn <= ? GROUP BY code HAVING count(*) >= ?""", [as_of, window, min_days]).fetchall()
    return {c: float(v) for c, v in rows if v}


def _pct(chg: pd.Series, bal: pd.Series) -> pd.Series:
    prev = bal - chg
    return (chg / prev).where(prev > 0)


def build_metrics(hist: pd.DataFrame, as_of: date | None = None,
                  avg_volume: dict[str, float] | None = None) -> pd.DataFrame:
    """基準日の銘柄ごとの需給指標を返す(純関数)。

    - ratio_bs: 信用倍率(買残÷売残。売残0は欠損)。torikumi: 取組比率(売残÷買残 %)
    - buy_chg_pct / sell_chg_pct: 前日比の増減率
    - buy_nd_pct: LOOKBACK_DAYS 営業日前(DB にある日付で数える)からの買残の増減率。履歴が足りなければ欠損
    - buy_pctile: その銘柄の履歴の中での買残の位置(0〜1。履歴5日未満は欠損)
    - days_to_clear: 買残を直近の平均出来高で割った日数(avg_volume があるとき)
    - signals: 需給の見立て(下の規則。ひとつも当てはまらなければ空)
    """
    if hist.empty:
        return pd.DataFrame()
    dates = sorted(hist["date"].unique())
    as_of = as_of or dates[-1]
    cur = hist[hist["date"] == as_of].copy().set_index("code")
    if cur.empty:
        return pd.DataFrame()

    cur["ratio_bs"] = (cur["buy_bal"] / cur["sell_bal"]).where(cur["sell_bal"] > 0)
    cur["torikumi"] = (cur["sell_bal"] / cur["buy_bal"] * 100).where(cur["buy_bal"] > 0)
    cur["buy_chg_pct"] = _pct(cur["buy_chg"], cur["buy_bal"])
    cur["sell_chg_pct"] = _pct(cur["sell_chg"], cur["sell_bal"])

    buy = hist.pivot(index="date", columns="code", values="buy_bal")
    idx = list(buy.index).index(as_of)
    if idx >= LOOKBACK_DAYS:
        prev = buy.iloc[idx - LOOKBACK_DAYS].reindex(cur.index)
        cur["buy_nd_pct"] = ((cur["buy_bal"] - prev) / prev).where(prev > 0)
    else:
        cur["buy_nd_pct"] = float("nan")
    past = buy.iloc[: idx + 1]
    n_obs = past.count().reindex(cur.index)
    rank = past.rank(pct=True).iloc[-1].reindex(cur.index)
    cur["buy_pctile"] = rank.where(n_obs >= 5)

    if avg_volume:
        vol = pd.Series(avg_volume).reindex(cur.index)
        cur["days_to_clear"] = (cur["buy_bal"] / vol).where(vol > 0)
    else:
        cur["days_to_clear"] = float("nan")

    cur["signals"] = [_signals(r) for _, r in cur.iterrows()]
    cur = cur.reset_index()
    return cur[["date", "code", "name", "flags", "market", "loan_type", "sell_bal", "buy_bal",
                "ratio_bs", "torikumi", "buy_chg_pct", "sell_chg_pct", "buy_nd_pct", "buy_pctile",
                "buy_listed_pct", "sell_listed_pct", "days_to_clear", "signals"]]


def _signals(r) -> str:
    """需給の見立て。いずれも「売買の目安」であり、買い・売りの判断そのものではない。"""
    out = []
    buy_big = pd.notna(r["buy_listed_pct"]) and r["buy_listed_pct"] >= MIN_LISTED_PCT
    sell_big = pd.notna(r["sell_listed_pct"]) and r["sell_listed_pct"] >= MIN_LISTED_PCT
    bc, sc = r["buy_chg_pct"], r["sell_chg_pct"]
    if buy_big and pd.notna(bc) and bc >= BUY_SURGE:
        out.append("買残急増")
    if buy_big and pd.notna(bc) and bc <= BUY_DROP:
        out.append("買残急減")
    if sell_big and pd.notna(sc) and sc >= SELL_SURGE:
        out.append("売残急増")
    if (buy_big or sell_big) and r["buy_chg"] < 0 < r["sell_chg"]:
        out.append("需給好転")        # 買残が減り、売残が増える(踏み上げの材料になりうる)
    if (buy_big or sell_big) and r["sell_chg"] < 0 < r["buy_chg"]:
        out.append("需給悪化")        # 買残が増え、売残が減る(将来の売り圧力)
    if (pd.notna(r["buy_listed_pct"]) and r["buy_listed_pct"] >= HEAVY_BUY_LISTED
            and pd.notna(r["ratio_bs"]) and r["ratio_bs"] >= HEAVY_RATIO):
        out.append("買残が重い")
    if sell_big and pd.notna(r["ratio_bs"]) and r["ratio_bs"] < SELL_HEAVY_RATIO:
        out.append("売り長")
    return "・".join(out)


_PCT_CHANGE = {"buy_chg_pct", "sell_chg_pct", "buy_nd_pct"}     # +12.3% の形
_PCT_LEVEL = {"buy_listed_pct", "sell_listed_pct"}               # すでに % の値(5.7 → 5.7%)


def _cell(col: str, v) -> str:
    if isinstance(v, float):
        if pd.isna(v):
            return ""
        if col in _PCT_CHANGE:
            return f"{v:+.1%}"
        if col in _PCT_LEVEL:
            return f"{v:.1f}%"
        if col == "buy_pctile":
            return f"{v:.0%}"
        return f"{v:,.1f}" if abs(v) < 1000 else f"{v:,.0f}"
    return str(v)


def _fmt(df: pd.DataFrame, cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(_cell(c, r[c]) for c in cols) + " |" for _, r in df.iterrows()]
    return "\n".join(lines)


def render_report(m: pd.DataFrame, top: int = 10) -> str:
    """指標を、見るための Markdown にする(純関数)。ETF などの投信は株の需給と性質が違うので除く。"""
    if m.empty:
        return "# 信用残の需給指標\n\nデータがありません。\n"
    stocks = m[~m["market"].str.contains("ETF|ETN|REIT|投信", regex=True, na=False)]
    d = m["date"].iloc[0]
    base = ["code", "name", "buy_bal", "sell_bal", "ratio_bs", "buy_chg_pct", "sell_chg_pct"]
    sections = [   # (見出し, 並べる列, 昇順か, 表の列, 規模の条件に使う列)
        ("買残が増えた(前日比。将来の売り圧力)", "buy_chg_pct", False, base + ["buy_listed_pct"], "buy_listed_pct"),
        ("買残が減った(前日比。整理・投げの進行)", "buy_chg_pct", True, base + ["buy_listed_pct"], "buy_listed_pct"),
        ("売残が増えた(前日比。売り長・踏み上げの材料)", "sell_chg_pct", False, base + ["sell_listed_pct"], "sell_listed_pct"),
        ("信用倍率が高い(買い長で重い)", "ratio_bs", False, base + ["buy_listed_pct"], "buy_listed_pct"),
        ("信用倍率が低い(売り長)", "ratio_bs", True, base + ["sell_listed_pct"], "sell_listed_pct"),
        ("買残が履歴で高い位置にある", "buy_pctile", False,
         ["code", "name", "buy_bal", "buy_pctile", "buy_nd_pct"], "buy_listed_pct"),
    ]
    out = [f"# 信用残の需給指標 {d}", "",
           f"対象 {len(stocks)} 銘柄(日々公表銘柄。ETF 等を除く)。単位は株。"
           f"残高が上場株数の{MIN_LISTED_PCT}%未満の側は順位・見立てから外しています。"
           "見立ては目安で、売買の判断そのものではありません。", ""]
    flagged = stocks[stocks["signals"] != ""]
    out += [f"## 見立てが付いた銘柄 {len(flagged)} 件", ""]
    if len(flagged):
        out += [_fmt(flagged.sort_values("buy_chg_pct", key=lambda s: s.abs(), ascending=False).head(top * 3),
                     ["code", "name", "ratio_bs", "buy_chg_pct", "sell_chg_pct", "signals"]), ""]
    for title, col, asc, cols, size_col in sections:
        sel = stocks[stocks[size_col] >= MIN_LISTED_PCT].dropna(subset=[col]).sort_values(col, ascending=asc).head(top)
        if col == "buy_pctile" and sel.empty:
            continue
        out += [f"## {title}", "", _fmt(sel, cols), ""]
    return "\n".join(out)


def write_report(con, out_dir: Path, as_of: date | None = None, top: int = 10) -> tuple[Path, Path] | None:
    """DB の信用残を解析して、Markdown のレポートと全銘柄の CSV を書く。データが無ければ None。"""
    hist = load_history(con, as_of)
    if hist.empty:
        return None
    day = as_of or max(hist["date"])
    m = build_metrics(hist, day, load_avg_volume(con, day))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md, csv = out_dir / f"margin_{day}.md", out_dir / f"margin_{day}.csv"
    md.write_text(render_report(m, top), encoding="utf-8")
    m.to_csv(csv, index=False, encoding="utf-8-sig")
    return md, csv
