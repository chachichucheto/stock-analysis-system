"""収集データと計算結果の DB(DuckDB)。スクリプトで作り直せるものだけを置く(docs/DESIGN.md §4)。"""
from __future__ import annotations

from pathlib import Path

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS news_item (
    news_id VARCHAR PRIMARY KEY,
    source VARCHAR, source_tier INTEGER, title VARCHAR, summary VARCHAR, url VARCHAR,
    language VARCHAR, published_at TIMESTAMPTZ, first_observed_at TIMESTAMPTZ NOT NULL,
    text_hash VARCHAR
);
CREATE TABLE IF NOT EXISTS disclosure (
    disclosure_id VARCHAR PRIMARY KEY,
    source VARCHAR,              -- tdnet | edinet
    code VARCHAR, company_name VARCHAR, title VARCHAR, doc_type VARCHAR, url VARCHAR,
    published_at TIMESTAMPTZ, first_observed_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS attention_daily (
    key VARCHAR, source VARCHAR, date DATE, value DOUBLE, first_observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (key, source, date)
);
CREATE TABLE IF NOT EXISTS universe_daily (
    date DATE, code VARCHAR, company_name VARCHAR, market VARCHAR, industry33 VARCHAR,
    status VARCHAR, first_observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS event (
    event_id VARCHAR PRIMARY KEY,
    title VARCHAR, first_observed_at TIMESTAMPTZ NOT NULL, news_ids VARCHAR[], entities VARCHAR[],
    media_count INTEGER, disclosure_type VARCHAR, novelty_hash VARCHAR
);
CREATE TABLE IF NOT EXISTS price_copy (
    code VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS calendar (
    code VARCHAR PRIMARY KEY, earnings_date DATE, earnings_estimated BOOLEAN,
    ex_rights_date DATE, credit_restriction VARCHAR, updated_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS fetch_log (
    source VARCHAR, fetched_at TIMESTAMPTZ, ok BOOLEAN, items INTEGER, message VARCHAR
);
"""


def connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(data_dir / "assoc.duckdb"))
    con.execute(DDL)
    return con
