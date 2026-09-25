"""収集器が共通で使うデータ構造(docs/DESIGN.md §5)。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class NewsItem:
    news_id: str
    source: str
    source_tier: int
    title: str
    summary: str
    url: str
    language: str
    published_at: datetime | None
    first_observed_at: datetime
    text_hash: str


@dataclass
class DisclosureItem:
    disclosure_id: str
    source: str  # tdnet | edinet
    code: str | None
    company_name: str
    title: str
    doc_type: str
    url: str
    published_at: datetime | None
    first_observed_at: datetime


@dataclass
class AttentionPoint:
    key: str
    source: str
    date: str  # YYYY-MM-DD(日本時間の日付)
    value: float
    first_observed_at: datetime
