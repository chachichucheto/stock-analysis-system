"""株価の読み取り。既存の株価DBは読み取り専用で使う(docs/DESIGN.md §2)。

S1 で既存DBの形式を確認したら、PriceSource を実装したクラスを1つ足し、
config の prices.source で切り替える。計算の部分は PriceSource だけに依存する。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol

import pandas as pd

COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class PriceSource(Protocol):
    def daily(self, code: str, start: date, end: date) -> pd.DataFrame:
        """date 昇順、列は COLUMNS。データが無ければ空の DataFrame を返す。"""
        ...


def _normalize(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    df = df.rename(columns=str.lower)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"株価データに必要な列がありません: {missing}")
    df = df[COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df.sort_values("date").reset_index(drop=True)


class CsvDirPriceSource:
    """銘柄ごとの CSV が1つのフォルダに入っている形式。"""

    def __init__(self, directory: Path, filename: str = "{code}.csv"):
        self.directory = Path(directory)
        self.filename = filename

    def daily(self, code: str, start: date, end: date) -> pd.DataFrame:
        path = self.directory / self.filename.format(code=code)
        if not path.exists():
            return pd.DataFrame(columns=COLUMNS)
        return _normalize(pd.read_csv(path), start, end)


class FramePriceSource:
    """テストや、他の形式から読み込んだデータを渡すための実装。"""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def daily(self, code: str, start: date, end: date) -> pd.DataFrame:
        if code not in self.frames:
            return pd.DataFrame(columns=COLUMNS)
        return _normalize(self.frames[code], start, end)


class YFinanceLivePriceSource:
    """yfinance から直接取る(既存DBに無い銘柄や、米国の1段目の確認用)。"""

    def daily(self, code: str, start: date, end: date) -> pd.DataFrame:
        import yfinance as yf

        symbol = f"{code}.T" if code.isdigit() or (len(code) == 4 and code[:3].isdigit()) else code
        df = yf.download(symbol, start=start, end=pd.Timestamp(end) + pd.Timedelta(days=1),
                         progress=False, auto_adjust=False)
        if df.empty:
            return pd.DataFrame(columns=COLUMNS)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index().rename(columns={"Date": "date"})
        return _normalize(df, start, end)


def price_source_from_config(cfg) -> PriceSource:
    section = cfg.section("prices")
    kind = section.get("source", "csv_dir")
    if kind == "csv_dir":
        directory = section.get("csv_dir")
        if not directory:
            raise ValueError("prices.csv_dir が未設定です(config/config.yaml)")
        return CsvDirPriceSource(Path(directory), section.get("csv_filename", "{code}.csv"))
    if kind == "yfinance_live":
        return YFinanceLivePriceSource()
    raise ValueError(f"未知の prices.source です: {kind}")
