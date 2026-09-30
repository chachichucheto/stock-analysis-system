"""GDELT DOC 2.0 API による世界の報道量の計測(docs/DESIGN.md §3)。

想定している形式
----------------
- `GET https://api.gdeltproject.org/api/v2/doc/doc?query={quote(keyword)}&mode=timelinevol&format=json`
  (期間を指定しなければ直近3か月程度が返る想定。DESIGN §6.2 の「過去90日」の足切りに使うため、
  期間はできるだけ広く取る)。
- レスポンス JSON:
  ```json
  {"timeline": [{"series": "timelinevol", "data": [
      {"date": "20260925000000", "value": 0.0123}, ...
  ]}]}
  ```
  `date` は `YYYYMMDDHHMMSS`(15分刻み)、`value` は報道量の割合(%)。UTC 基準の想定。
- 日次に落とすため、同じ日付(先頭8桁)の `value` を平均する。

ローカルで最初に確認すべき点
----------------------------
- 実際のキー名(`timeline`/`data`/`date`/`value` のスペルや入れ子)。バージョンによって
  `mode=timelinevolraw` の方が生の記事数に近い場合がある。
- 日本語キーワードのクエリが GDELT でヒットするか(GDELT は多言語対応だが英語クエリの方が
  安定する可能性がある。必要なら英訳したキーワードを別途 config に用意する)。
- レート制限(1秒あたりの呼び出し回数の上限)。
  【2026-09-30 実確認】公式の目安は5秒に1回だが、6秒空けても 429 になることがある。
  日本語キーワード(「半導体」)は接続できたが、timeline が空(0件)だった。英語キーワードは取得制限に
  当たり確認できていない。注目度の足切りへは未配線のため、優先度は低い(docs の点検結果参照)。
"""
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote

from assoc.ingest.common import RateLimiter, upsert_attention
from assoc.ingest.models import AttentionPoint
from assoc.timeutil import utcnow

DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=timelinevol&format=json"


def parse_timeline_json(
    data: dict, *, keyword: str, first_observed_at: datetime | None = None
) -> list[AttentionPoint]:
    """timelinevol のレスポンス(dict)を日次の AttentionPoint にする(純関数)。"""
    first_observed_at = first_observed_at or utcnow()
    buckets: dict[str, list[float]] = {}
    for series in data.get("timeline", []) or []:
        for point in series.get("data", []) or []:
            date_str = str(point.get("date", ""))[:8]
            if len(date_str) != 8 or not date_str.isdigit():
                continue
            try:
                value = float(point.get("value", 0.0))
            except (TypeError, ValueError):
                continue
            buckets.setdefault(date_str, []).append(value)
    points = []
    for date_str, values in sorted(buckets.items()):
        d = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        points.append(
            AttentionPoint(
                key=keyword, source="gdelt", date=d.isoformat(),
                value=sum(values) / len(values), first_observed_at=first_observed_at,
            )
        )
    return points


def fetch_gdelt(cfg, con, keywords: list[str] | None = None) -> int:
    """config の collect.google_news_queries(+ keywords)相当のキーワードで GDELT を取得する。

    keywords を明示的に渡さない場合、collect.google_news_queries を流用する
    (「注目度」計測のキーワードは1つに揃える方針)。
    """
    collect = cfg.section("collect")
    kws = list(dict.fromkeys(keywords if keywords is not None else (collect.get("google_news_queries") or [])))
    # GDELT は「5秒に1回」が公式の目安だが、実測では6秒空けても 429 になった(2026-09-30)。
    # 余裕を持って10秒空け、1キーワードの失敗で全体を止めない(全部失敗したときだけ失敗として扱う)。
    limiter = RateLimiter(
        max(float(collect.get("gdelt_interval_sec", 10.0)), 5.0), collect.get("user_agent", "assoc-research/0.1")
    )
    total, failed, last_error = 0, [], None
    for kw in kws:
        try:
            resp = limiter.get(DOC_API_URL.format(q=quote(kw)))
            points = parse_timeline_json(resp.json(), keyword=kw)
            total += upsert_attention(con, points)
        except Exception as e:
            failed.append(kw)
            last_error = e
    if kws and len(failed) == len(kws):
        raise RuntimeError(f"GDELT: 全キーワードで取得に失敗({len(failed)}件)") from last_error
    return total
