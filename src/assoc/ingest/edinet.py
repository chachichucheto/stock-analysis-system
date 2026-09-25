"""EDINET API v2 による大量保有報告書・有価証券報告書などの収集(docs/DESIGN.md §3)。

想定している形式
----------------
- 書類一覧:`GET https://api.edinet-fsa.go.jp/api/v2/documents.json
  ?date=YYYY-MM-DD&type=2&Subscription-Key=...`
  `type=2` はメタデータ+提出書類一覧(`type=1` はメタデータのみ)。
- レスポンス JSON の形:
  ```json
  {"metadata": {"resultset": {"count": 123}, "status": "200"},
   "results": [
     {"docID": "S100XXXX", "secCode": "12340", "filerName": "株式会社サンプル",
      "docTypeCode": "350", "docDescription": "大量保有報告書",
      "submitDateTime": "2026-09-25 15:00", ...}
   ]}
  ```
- `secCode` は5桁(証券コード4桁+検査用の"0")。4桁の証券コードは末尾の0を除いたもの。
- `docTypeCode` は例:030=有価証券報告書、040=四半期報告書、050=半期報告書、
  350=大量保有報告書、360=変更報告書(大量保有)、140=臨時報告書。
  対象は `TARGET_DOC_TYPES` で絞る(大量保有報告書とその変更報告書、有価証券報告書、臨時報告書)。
- PDF 本体は取得しない(見出し相当の `docDescription` だけを保存する)。保存する URL は
  ダウンロード API の参照用 URL とし、**API キーは URL に含めない**(DB に平文で残さないため)。

ローカルで最初に確認すべき点
----------------------------
- 実際のフィールド名(`docTypeCode` のコード値が版によって変わっていないか)。
- `metadata.status` が "200" 以外(400 など)のときのエラーメッセージの形。
- 大量保有報告書の対象範囲(EDINET は上場株以外の届出も混在するため、`secCode` が
  空の行の扱い)。
- 1日あたりの呼び出し回数の上限(無料枠のレート制限)。
- API キー未登録時の挙動(`config/config.yaml` の `edinet.api_key` が空ならスキップする)。
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from assoc.ingest.common import RateLimiter, log_fetch, sha256_hex, upsert_disclosures
from assoc.ingest.models import DisclosureItem
from assoc.timeutil import jst_date, utcnow

DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
JST = ZoneInfo("Asia/Tokyo")

# 対象とする書類種別(docTypeCode)。DESIGN.md §6.2 の「強い種類の開示」に対応する。
TARGET_DOC_TYPES = {
    "350": "大量保有報告書",
    "360": "変更報告書",
    "030": "有価証券報告書",
    "140": "臨時報告書",
}


def _sec_code_to_ticker(sec_code: str | None) -> str | None:
    if not sec_code:
        return None
    sec_code = sec_code.strip()
    if len(sec_code) == 5 and sec_code.isdigit():
        return sec_code[:4]
    return sec_code or None


def _parse_submit_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=JST)


def parse_documents_json(
    data: dict, *, doc_types: dict[str, str] | None = None, first_observed_at: datetime | None = None
) -> list[DisclosureItem]:
    """documents.json のレスポンス(dict)を DisclosureItem のリストにする(純関数)。"""
    doc_types = doc_types if doc_types is not None else TARGET_DOC_TYPES
    first_observed_at = first_observed_at or utcnow()
    items: list[DisclosureItem] = []
    for r in data.get("results", []) or []:
        code = r.get("docTypeCode")
        if code not in doc_types:
            continue
        doc_id = r.get("docID", "")
        if not doc_id:
            continue
        items.append(
            DisclosureItem(
                disclosure_id=sha256_hex(f"edinet:{doc_id}"),
                source="edinet",
                code=_sec_code_to_ticker(r.get("secCode")),
                company_name=r.get("filerName", "") or "",
                title=r.get("docDescription") or doc_types.get(code, code),
                doc_type=code,
                url=f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}?type=2",
                published_at=_parse_submit_datetime(r.get("submitDateTime")),
                first_observed_at=first_observed_at,
            )
        )
    return items


def fetch_edinet(cfg, con, day: date | None = None) -> int:
    """指定日(省略時は当日、日本時間)の書類一覧を取得する。API キー未設定ならスキップする。"""
    day = day or jst_date(utcnow())
    api_key = (cfg.section("edinet") or {}).get("api_key", "")
    if not api_key:
        log_fetch(con, "edinet", True, 0, "edinet.api_key が未設定のためスキップ")
        return 0
    collect = cfg.section("collect")
    limiter = RateLimiter(
        float(collect.get("request_interval_sec", 1.0)), collect.get("user_agent", "assoc-research/0.1")
    )
    resp = limiter.get(
        DOCUMENTS_URL, params={"date": day.isoformat(), "type": 2, "Subscription-Key": api_key}
    )
    data = resp.json()
    items = parse_documents_json(data)
    return upsert_disclosures(con, items)
