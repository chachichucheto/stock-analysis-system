"""信用残(JPX)の取得・保存・需給指標の算出。

取得するもの(いずれも毎営業日の16時ごろ掲載。直近分しか残らないので、毎日取って DB に貯める)
--------------------------------------------------------------------------------------
1. 銘柄別信用取引残高 `YYYYMMDD_mtall.pdf`(全銘柄・約4,200。ページ `margin/01.html`)。主データ。
   PDF のみの配布。ページは横向き(90度回転)で、行が x 方向・列が y 方向に並ぶ。罫線は使わず、
   単語の座標から読む(pdfplumber の表抽出は約220秒、座標方式は約4秒)。1銘柄は「株数」行と「金額」行の2行で、
   必要な数字は株数行だけで足りる。
2. 日々公表銘柄等信用取引残高 `YYYYMMDD_mtdaily.xlsx`(約400銘柄。ページ `margin/index.html`)。
   規制の印(規/日/監/株/喚)がここにしか無いので、PDF の銘柄に同じ日・同じコードで付ける。
   PDF が読めなかった日は、この xlsx だけを保存する(fetch_log の jpx_margin_all に失敗を残す)。

列の意味(両方共通): 売残高・前日比・上場比、買残高・前日比・上場比、売残/買残それぞれの一般信用・制度信用。
単位は1株(ETF 等は1口を1株)。上場比は ETF が `*`(欠損として保存)。5桁コードの末尾0を除いた4桁が証券コード
(新形式は `283A0` → `283A`)。

xlsx の配置: B列(`申込み現在` の行)に基準日、銘柄行は A=単位 B=規制 C=貸株 D=名前 E=市場 F=制/貸/他 G=コード H=ISIN、
I〜K=売残・前日比・上場比、L〜N=買残・前日比・上場比、O=取組比率、P〜W=内訳。

ローカルで最初に確認すべき点
----------------------------
- JPX が形式を変えると PDF が読めなくなる(銘柄数 3,000 未満・基準日の不一致は失敗として扱い、xlsx に切り替える)。
- 過去のファイルは取れない。手元に溜めた分は `margin import` で取り込む。

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
ALL_URL = "https://www.jpx.co.jp/markets/statistics-equities/margin/01.html"   # 銘柄別信用取引残高(全銘柄・PDF)

_ISIN = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")
_DAILY_LINK = re.compile(r'href="([^"]*?(\d{8})_mtdaily\.xlsx)"')
_ALL_LINK = re.compile(r'href="([^"]*?(\d{8})_mtall\.pdf)"')
_ASOF = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*申込み現在")

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


def find_daily_url(html: str, base: str = INDEX_URL, *, pattern=_DAILY_LINK) -> str:
    """公表ページの HTML から、最新日のファイル(既定は mtdaily.xlsx)の URL を探す。"""
    links = {m.group(2): m.group(1) for m in pattern.finditer(html)}
    if not links:
        raise ValueError("公表ページに目的のファイルへのリンクが見つかりません")
    return urljoin(base, links[max(links)])


def find_all_url(html: str, base: str = ALL_URL) -> str:
    """銘柄別信用取引残高のページから、最新日の mtall.pdf(全銘柄)の URL を探す。"""
    return find_daily_url(html, base, pattern=_ALL_LINK)


# mtall.pdf は横向き(ページが90度回転)で、行が x 方向、列が y 方向に並ぶ。列の y 座標は固定
_PDF_VALUE_Y = (551, 510, 479, 438, 396, 365, 324, 283, 241, 200, 158, 117, 76, 34)   # 売残,前日比,上場比,買残,前日比,上場比,
#                                                                                      一般売,前日比,制度売,前日比,一般買,前日比,制度買,前日比
_PDF_ISIN_Y, _PDF_CODE_Y, _PDF_LOAN_Y, _PDF_NAME_Y = 616, 649, 682, 690
_PDF_Y_TOL = 6.0
_PDF_ROW_TOL = 2.5
_MARKETS = ("投信等", "プライム", "スタンダード", "グロース")   # 投信等を先に(ETF の名前に「グロース」等が入るため)


def parse_all_pdf(raw: bytes | str | Path) -> pd.DataFrame:
    """mtall.pdf(銘柄別信用取引残高。全銘柄)を、列が COLUMNS の DataFrame にする。

    表の罫線は使わず、単語の座標から読む(pdfplumber の表抽出は全109ページで約220秒かかったため)。
    1銘柄が「株数」行と「金額」行の2行で、必要な数字・コード・ISIN・市場は株数行だけで足りる。
    flags は PDF に無いので空(merge_flags で日々公表銘柄の印を足す)。
    """
    import pymupdf

    doc = pymupdf.open(stream=raw, filetype="pdf") if isinstance(raw, (bytes, bytearray)) else pymupdf.open(raw)
    as_of, out = None, []
    for page in doc:
        if as_of is None:
            m = _ASOF.search(page.get_text())
            if m:
                as_of = date(int(m[1]), int(m[2]), int(m[3]))
        out += rows_from_words(page.get_text("words"))     # (x0, y0, x1, y1, text, ...)
    if as_of is None:
        raise ValueError("信用残 PDF に基準日(申込み現在)が見つかりません。JPX の形式が変わった可能性があります")
    if not out:
        raise ValueError("信用残 PDF に銘柄の行が見つかりません。JPX の形式が変わった可能性があります")
    df = pd.DataFrame(out, columns=COLUMNS)
    df["date"] = as_of
    return df


def rows_from_words(words: list) -> list[dict]:
    """1ページ分の単語(座標つき)から、銘柄ごとの行(date 以外の列)を取り出す(純関数)。"""
    out = []
    for x0, y0, _, _, text, *_ in words:
        if not text.startswith("株数"):
            continue
        line = [w for w in words if abs(w[0] - x0) <= _PDF_ROW_TOL]
        by_y = sorted(((w[1], w[4]) for w in line), key=lambda t: t[0])
        isin = next((t for y, t in by_y if abs(y - _PDF_ISIN_Y) < _PDF_Y_TOL and _ISIN.match(t)), None)
        code = next((t for y, t in by_y if abs(y - _PDF_CODE_Y) < _PDF_Y_TOL), None)
        if not (isin and code):
            continue
        vals: dict[int, float | None] = {}
        numbers = [(y, t) for y, t in by_y if y < _PDF_ISIN_Y - 15 and t != "▲"]
        for y, t in numbers:
            col = min(range(14), key=lambda i: abs(_PDF_VALUE_Y[i] - y))
            if abs(_PDF_VALUE_Y[col] - y) < _PDF_Y_TOL:
                vals[col] = _pdf_value(t)
        for y, t in by_y:                   # ▲ は数字のすぐ左(y が大きい側)にあり、その数字を負にする
            if t == "▲":
                below = [c for c in vals if _PDF_VALUE_Y[c] < y and y - _PDF_VALUE_Y[c] < 30]
                if below and vals[max(below, key=lambda c: _PDF_VALUE_Y[c])] is not None:
                    c = max(below, key=lambda c: _PDF_VALUE_Y[c])
                    vals[c] = -abs(vals[c])
        text_cells = [t for y, t in by_y if y >= _PDF_NAME_Y and y < 790]
        joined = "".join(text_cells)
        market = next((mk for mk in _MARKETS if mk in joined), "")
        name = " ".join(t for t in text_cells if t not in _MARKETS).replace("\u3000", " ").strip()
        for mk in _MARKETS:
            name = name.replace(mk, "").strip()
        loan_cell = "".join(t for y, t in by_y if abs(y - _PDF_LOAN_Y) < _PDF_Y_TOL)
        g = vals.get
        out.append({
            "date": None, "code": normalize_code(code), "name": name, "flags": "",
            "market": market, "loan_type": "貸" if "貸" in loan_cell else "制" if "制" in loan_cell else "他",
            "sell_bal": g(0), "sell_chg": g(1), "sell_listed_pct": g(2),
            "buy_bal": g(3), "buy_chg": g(4), "buy_listed_pct": g(5),
            "sell_general": g(6), "sell_system": g(8), "buy_general": g(10), "buy_system": g(12),
        })
    return out


def _pdf_value(t: str) -> float | None:
    return _num(t.replace("%", ""))


def merge_flags(all_df: pd.DataFrame, daily_df: pd.DataFrame) -> pd.DataFrame:
    """全銘柄(PDF)に、日々公表銘柄(xlsx)にだけある規制の印(規/日/監…)を、同じ日・同じコードで付ける。"""
    if all_df.empty or daily_df.empty or all_df["date"].iloc[0] != daily_df["date"].iloc[0]:
        return all_df
    marks = dict(zip(daily_df["code"], daily_df["flags"]))
    out = all_df.copy()
    out["flags"] = [marks.get(c, f) for c, f in zip(out["code"], out["flags"])]
    return out


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
    """最新日の信用残を取得して margin_daily に保存する。保存した銘柄数を返す。

    全銘柄の PDF(mtall.pdf)を主とし、日々公表銘柄の xlsx から規制の印を足す。
    PDF が読めなかったときは、xlsx(日々公表銘柄のみ)だけを保存して、その旨を fetch_log に残す。
    url を渡したときは、その xlsx だけを取り込む。
    """
    collect = cfg.section("collect")
    limiter = RateLimiter(float(collect.get("request_interval_sec", 1.0)),
                          collect.get("user_agent", "assoc-research/0.1"))
    if url is not None:
        df = parse_daily_excel(limiter.get(url, timeout=60).content)
    else:
        index = collect.get("jpx_margin_index_url") or INDEX_URL
        daily = parse_daily_excel(limiter.get(find_daily_url(limiter.get(index).text, index), timeout=60).content)
        page = collect.get("jpx_margin_all_url") or ALL_URL
        try:
            all_df = parse_all_pdf(limiter.get(find_all_url(limiter.get(page).text, page), timeout=180).content)
            df = merge_flags(all_df, daily)
            if len(df) < 3000 or df["date"].iloc[0] != daily["date"].iloc[0]:
                raise ValueError(f"全銘柄 PDF が不完全です({len(df)} 銘柄、基準日 {df['date'].iloc[0]})")
        except Exception as e:  # noqa: BLE001 - PDF の失敗で日々公表銘柄まで失わない
            log_fetch(con, "jpx_margin_all", False, 0, f"{type(e).__name__}: {e}")
            df = daily
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
           f"対象 {len(stocks)} 銘柄(ETF 等を除く。全銘柄の信用残が取れた日は全銘柄、PDF が読めなかった日は日々公表銘柄のみ)。単位は株。"
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
