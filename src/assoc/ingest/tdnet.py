"""TDnet(適時開示情報閲覧サービス)の公開一覧の収集(docs/DESIGN.md §3・§11)。

**過去分は約1か月で消えるため、最優先で収集を始める(docs/CONCEPT.md §14.1)。**

想定している形式
----------------
- 一覧ページ:`https://www.release.tdnet.info/inbs/I_list_{page:03d}_{YYYYMMDD}.html`
  (1ページ目が `I_list_001_YYYYMMDD.html`、以降ページ送り)。当日分が存在しない、または
  ページ数を超えると 404、もしくは行数0のページが返る想定。
- 文字コードは Shift_JIS(cp932)を想定(TDnet の古くからの仕様)。UTF-8 化されている
  可能性もあるため、`<meta charset>` を見てから cp932 にフォールバックする。
- 一覧はテーブル(`<tr>` の並び)で、各行が1件の開示。列は概ね次の構成
  (実際のクラス名は開発時点の記憶によるもので確認が要る):
  - `kjTime` 開示時刻(HH:MM、日本時間)
  - `kjCode` 銘柄コード(4桁、まれに5桁)
  - `kjName` 会社名
  - `kjTitle` 開示タイトル(`<a href="...">` で PDF へのリンク。相対パス)
  - `kjPlace` 上場市場
  - `kjHistroy`(原文ママの綴りだった記憶がある)訂正・番号の履歴
- PDF の相対 URL は `https://www.release.tdnet.info/inbs/` を前置して絶対 URL にする。

ローカルで最初に確認すべき点
----------------------------
- 実際のページの HTML 構造(td の class 名、列の並び)。上記は記憶に基づく推定であり、
  ズレていれば `_ROW_RE` / `_TD_RE` の正規表現を直す(またはこの HTML 用に
  BeautifulSoup を requirements.txt に追加して書き換える方が保守しやすい可能性がある)。
- ページ送りの終端の判定方法(404 になるか、0件の空ページが返るか)。
- 銘柄コードが5桁(英字を含む新形式)になっていないか。
- 文字コードが本当に Shift_JIS か(UTF-8 化されていれば `_decode` の優先順位を直す)。
- 土日・祝日はページが存在しない(0件)ことの確認。
"""
from __future__ import annotations

from assoc.timeutil import is_business_day

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from assoc.ingest.common import RateLimiter, sha256_hex, upsert_disclosures
from assoc.ingest.models import DisclosureItem
from assoc.timeutil import utcnow

BASE_URL = "https://www.release.tdnet.info/inbs/"
LIST_URL = BASE_URL + "I_list_{page:03d}_{ymd}.html"
JST = ZoneInfo("Asia/Tokyo")

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r'<td[^>]*class="([^"]*)"[^>]*>(.*?)</td>', re.IGNORECASE | re.DOTALL)
_A_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).replace("&nbsp;", " ").strip()


def decode_tdnet_html(raw: bytes) -> str:
    """TDnet の HTML をデコードする。UTF-8 → Shift_JIS(cp932) の順に試す。"""
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace")


@dataclass
class _Row:
    time_text: str
    code: str
    company_name: str
    title: str
    href: str


def _parse_row(row_html: str) -> _Row | None:
    cells = {cls.split()[0]: _strip_tags(body) for cls, body in _TD_RE.findall(row_html)}
    if "kjName" not in cells or "kjCode" not in cells:
        return None
    title_html = ""
    for cls, body in _TD_RE.findall(row_html):
        if cls.split()[0] == "kjTitle":
            title_html = body
            break
    href, title_text = "", cells.get("kjTitle", "")
    m = _A_RE.search(title_html)
    if m:
        href = m.group(1).strip()
        title_text = _strip_tags(m.group(2))
    return _Row(
        time_text=cells.get("kjTime", ""),
        code=cells.get("kjCode", "").strip(),
        company_name=cells.get("kjName", "").strip(),
        title=title_text,
        href=href,
    )


def parse_list_page(
    html: str, *, list_date: date, first_observed_at: datetime | None = None
) -> list[DisclosureItem]:
    """一覧ページ(1ページ分)の HTML を DisclosureItem のリストにする(純関数)。"""
    first_observed_at = first_observed_at or utcnow()
    items: list[DisclosureItem] = []
    for row_html in _ROW_RE.findall(html):
        row = _parse_row(row_html)
        if row is None or not row.company_name:
            continue
        url = row.href if row.href.startswith("http") else BASE_URL + row.href.lstrip("/")
        published_at = None
        m = re.match(r"^(\d{1,2}):(\d{2})$", row.time_text.strip())
        if m:
            published_at = datetime(
                list_date.year, list_date.month, list_date.day,
                int(m.group(1)), int(m.group(2)), tzinfo=JST,
            )
        items.append(
            DisclosureItem(
                disclosure_id=sha256_hex(url or f"{row.code}:{row.title}:{row.time_text}"),
                source="tdnet",
                code=row.code or None,
                company_name=row.company_name,
                title=row.title,
                doc_type="",  # TDnet の一覧には種別コードが無いため、タイトルの文言で判定する(events/gate.py)
                url=url,
                published_at=published_at,
                first_observed_at=first_observed_at,
            )
        )
    return items


def fetch_day(cfg, con, day: date, limiter: RateLimiter | None = None, max_pages: int = 30) -> int:
    """指定日の一覧をページ送りしながら取得する。空ページ(0件)が出たら終了する。"""
    collect = cfg.section("collect")
    limiter = limiter or RateLimiter(
        float(collect.get("request_interval_sec", 1.0)), collect.get("user_agent", "assoc-research/0.1")
    )
    total = 0
    ymd = day.strftime("%Y%m%d")
    for page in range(1, max_pages + 1):
        url = LIST_URL.format(page=page, ymd=ymd)
        try:
            resp = limiter.get(url)
        except Exception as e:
            # 2ページ目以降の失敗(404)はページ送りの終わり。1ページ目の失敗は、平日なら収集の失敗として扱う
            # (「成功・0件」と記録すると、3回連続失敗の警告が出なくなるため)
            status = getattr(getattr(e, "response", None), "status_code", None)
            if page == 1 and is_business_day(day) and status != 404:
                raise
            break
        html = decode_tdnet_html(resp.content)
        items = parse_list_page(html, list_date=day)
        if not items:
            break
        total += upsert_disclosures(con, items)
    return total


def fetch_tdnet(cfg, con, start: date | None = None, end: date | None = None) -> int:
    """start〜end(日本時間、両端含む)の TDnet 一覧を取得する。省略時は当日のみ。"""
    from assoc.timeutil import jst_date

    start = start or jst_date(utcnow())
    end = end or start
    limiter = RateLimiter(
        float(cfg.section("collect").get("request_interval_sec", 1.0)),
        cfg.section("collect").get("user_agent", "assoc-research/0.1"),
    )
    total, failed, last_error = 0, [], None
    d = start
    while d <= end:
        try:
            total += fetch_day(cfg, con, d, limiter=limiter)
        except Exception as e:          # 1日の失敗で、残りの日の取得を止めない
            failed.append(d.isoformat())
            last_error = e
        d += timedelta(days=1)
    if failed:
        raise RuntimeError(f"TDnet の取得に失敗した日: {', '.join(failed)}(取得できた件数 {total})") from last_error
    return total
