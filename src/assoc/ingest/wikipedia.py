"""Wikimedia pageviews API による一般の関心の計測(docs/DESIGN.md §3)。

想定している形式
----------------
- `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
  {project}/all-access/user/{article}/daily/{start:YYYYMMDD}/{end:YYYYMMDD}`
  - `project` は `ja.wikipedia`(日本語版)または `en.wikipedia`(英語版)。
  - `article` は記事名(スペースはアンダースコアに置換して URL エンコードする)。
  - Wikimedia のポリシー上、`User-Agent` ヘッダの送付が必須(RateLimiter が付与する)。
- レスポンス JSON:
  ```json
  {"items": [
    {"project": "ja.wikipedia", "article": "半導体", "granularity": "daily",
     "timestamp": "2026082500", "access": "all-access", "agent": "user", "views": 12345}
  ]}
  ```
  `timestamp` は `YYYYMMDDHH`(daily 粒度では末尾は常に `00`)。
- 集計の反映には1〜2日のラグがあるため、直近の日付は欠けることがある
  (該当日を除いて保存すればよい。0件は attention_daily に書かない)。

ローカルで最初に確認すべき点
----------------------------
- 記事名の表記ゆれ(リダイレクトされる別名では views が別集計になる可能性がある。
  正式なタイトル表記を使う)。
- 直近何日分が未集計か(実行時刻によって変わる)。
- 英語版記事名との対応表をどこで管理するか(テーマごとの記事名は `THEMES.md` 側の管轄になる見込み)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote

from assoc.ingest.common import RateLimiter, upsert_attention
from assoc.ingest.models import AttentionPoint
from assoc.timeutil import jst_date, utcnow

PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "{project}/all-access/user/{article}/daily/{start}/{end}"
)

PROJECT_SOURCE = {"ja": "wiki_ja", "en": "wiki_en"}


@dataclass
class WikiArticle:
    title: str
    project: str = "ja"  # "ja" | "en"
    key: str | None = None  # attention_daily の key。省略時は title を使う


def parse_pageviews_json(
    data: dict, *, article: WikiArticle, first_observed_at: datetime | None = None
) -> list[AttentionPoint]:
    """pageviews API のレスポンス(dict)を AttentionPoint のリストにする(純関数)。"""
    first_observed_at = first_observed_at or utcnow()
    source = PROJECT_SOURCE.get(article.project, f"wiki_{article.project}")
    key = article.key or article.title
    points = []
    for item in data.get("items", []) or []:
        ts = str(item.get("timestamp", ""))
        if len(ts) < 8:
            continue
        d = date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
        views = item.get("views")
        if views is None:
            continue
        points.append(
            AttentionPoint(key=key, source=source, date=d.isoformat(), value=float(views),
                            first_observed_at=first_observed_at)
        )
    return points


def fetch_wikipedia(
    cfg, con, articles: list[WikiArticle], *, start: date | None = None, end: date | None = None
) -> int:
    """articles(記事名の一覧)の日次閲覧数を取得する。

    既定の期間は、直近3日〜2日前(集計のラグを避けるため)。過去分を取り直したいときは
    start/end を明示する(2015年7月以降は取得できる。docs/DESIGN.md §3)。
    """
    today = jst_date(utcnow())
    end = end or (today - timedelta(days=1))
    start = start or (end - timedelta(days=2))
    collect = cfg.section("collect")
    limiter = RateLimiter(
        float(collect.get("request_interval_sec", 1.0)), collect.get("user_agent", "assoc-research/0.1")
    )
    total = 0
    for article in articles:
        project = f"{article.project}.wikipedia"
        url = PAGEVIEWS_URL.format(
            project=project,
            article=quote(article.title.replace(" ", "_"), safe=""),
            start=start.strftime("%Y%m%d"),
            end=end.strftime("%Y%m%d"),
        )
        resp = limiter.get(url)
        points = parse_pageviews_json(resp.json(), article=article)
        total += upsert_attention(con, points)
    return total
