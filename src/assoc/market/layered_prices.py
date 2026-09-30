"""既存システムの株価データを読み取り専用で読み、連想側の空白補完と重ねて使う株価の読み方。

背景(2026-09-30 に既存システムを読み取りで確認した事実。docs/PROPOSAL_HYBRID_AND_SHARING.md):
- 全銘柄の日足 `daily_ohlcv.parquet`:3,734銘柄、2004年〜**2026-07-24 で更新停止**
- 日次で更新されている `tactical_universe_ohlcv.parquet`:555銘柄、2026-06-02〜
- **7/25以降の全銘柄は、誰も取得していない**(連想側が空白分だけ取得する)
- **2つの既存ファイルは、同じ期間でも終値が食い違う**(重なった555銘柄中185銘柄で、期間全体が
  一定倍率にずれていた。株式分割の調整のかかり方の違いと考えられる。285A は3倍、5706 は約10倍)。
  さらに 7082 の日足は 16.8億円など壊れた値を含む。
  → そのままつなぐと、つなぎ目で偽の暴落・暴騰が出て、騰落率の計算(的中判定・織り込み度)を壊す。

この部品がやること:
1. 層を「新しい順」に重ねる(既定: 既存の日次555銘柄 → 連想側の空白補完 → 既存の全銘柄履歴)。
2. **つなぎ目の補正**:重なった日の終値の比(中央値)で、古い側の倍率をそろえる。比が期間内で安定しない
   (分割が重なりの途中にある等)ときは、古い側を使わず警告する(欠けた方が、壊れた値より安全)。
   重なりが無いときは、つなぎ目の変化が大きすぎれば使わず警告する。
3. **品質ゲート**:株価が500万円超・0以下・欠損の行を捨てる(既存システムの CIS Engine と同じ基準)。
4. **鮮度チェック**:各層の最新日付と経過日数を返す(`status()`)。古いデータを黙って使わない。
5. 既存ファイルは**読み取り専用**で開く。書き換え中(更新時刻が新しい)なら少し待つ。

既存ファイルには一切書き込まない。連想側が書くのは、自分のデータフォルダの空白補完ファイルだけ。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from assoc.market.prices import COLUMNS, _normalize

PRICE_CAP = 5_000_000.0        # これを超える株価は壊れた値とみなす(CIS Engine の品質ゲートと同じ)
SPREAD_LIMIT = 1.10            # 重なりの日々で終値の比が、最大/最小でこれを超えたら「安定しない」
SEAM_JUMP_LIMIT = 0.35         # 重なりが無いとき、つなぎ目の1日の変化がこれを超えたら使わない
SETTLE_SECONDS = 60            # 更新時刻がこれより新しいファイルは、書き換え中かもしれないので待つ
_PRICE_COLS = ["open", "high", "low", "close"]


@dataclass
class Layer:
    name: str
    path: Path

    def exists(self) -> bool:
        return self.path.exists()


def quality_gate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """壊れた行を捨てる。(残った行, 捨てた行数)を返す。"""
    if df.empty:
        return df, 0
    df = df.copy()
    for c in _PRICE_COLS + ["volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    ok = df["close"].notna() & (df["close"] > 0) & (df["close"] <= PRICE_CAP)
    for c in ("open", "high", "low"):        # 始値・高値・安値は欠けていても捨てず、上限だけ見る
        ok &= ~(df[c] > PRICE_CAP)
    return df[ok].reset_index(drop=True), int((~ok).sum())


def _attach(base: pd.DataFrame, old: pd.DataFrame, name: str, code: str, warns: list[str]) -> pd.DataFrame:
    """base(新しい側)に old(古い側)をつなぐ。倍率をそろえ、安全でなければ old を使わない。"""
    if old.empty:
        return base
    if base.empty:
        return old
    b = base.set_index("date")["close"]
    o = old.set_index("date")["close"]
    common = b.index.intersection(o.index)
    if len(common) >= 1:          # 重なりが1〜2日でも、倍率はそろえる(0日のときだけつなぎ目の検査へ)
        ratios = (b.loc[common] / o.loc[common]).astype(float)
        r = float(np.median(ratios))
        lo, hi = float(ratios.min()), float(ratios.max())
        if not np.isfinite(r) or r <= 0 or lo <= 0 or hi / lo > SPREAD_LIMIT:
            warns.append(f"{code}: {name} と重なる日の終値の比が安定せず(最大/最小={hi / lo if lo > 0 else float('inf'):.2f})、"
                         f"{name} は使いません")
            return base
        if abs(r - 1.0) > 0.0005:
            old = old.copy()
            for c in _PRICE_COLS:
                old[c] = old[c] * r
            old["volume"] = old["volume"] / r
    elif old["date"].max() < base["date"].min():
        jump = float(base["close"].iloc[0]) / float(old["close"].iloc[-1])
        if abs(jump - 1.0) > SEAM_JUMP_LIMIT:
            warns.append(f"{code}: {name} とのつなぎ目で終値が {jump:.2f} 倍に変わり、重なる日も無いため {name} は使いません")
            return base
    extra = old[~old["date"].isin(base["date"])]
    if extra.empty:
        return base
    return pd.concat([extra, base]).sort_values("date").reset_index(drop=True)


class LayeredPriceSource:
    """layers は「新しい・優先する順」。先頭が基準で、後ろの層は倍率をそろえてから足りない日だけ足す。"""

    def __init__(self, layers: list[Layer], cfg=None, max_stale_days: int = 10):
        self.layers = layers
        self.cfg = cfg                      # 渡されていれば、古い銘柄をその場で自動補完する
        self.max_stale_days = max_stale_days
        self.warnings: list[str] = []
        self.dropped_rows = 0
        self._autofill_tried: set[str] = set()

    def _read(self, layer: Layer, code: str) -> pd.DataFrame:
        if not layer.exists():
            return pd.DataFrame(columns=COLUMNS)
        import duckdb

        for attempt in range(4):
            age = time.time() - layer.path.stat().st_mtime
            if age < SETTLE_SECONDS:                      # 書き換え中かもしれない
                time.sleep(SETTLE_SECONDS - age + 1)
            try:
                con = duckdb.connect()
                try:
                    df = con.execute(
                        "SELECT date, open, high, low, close, volume FROM read_parquet(?) WHERE code = ? ORDER BY date",
                        [str(layer.path), code]).fetchdf()
                finally:
                    con.close()
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df, dropped = quality_gate(df)
        self.dropped_rows += dropped
        return df

    def _merged(self, code: str) -> pd.DataFrame:
        warns: list[str] = []
        merged = pd.DataFrame(columns=COLUMNS)
        for layer in self.layers:
            frame = self._read(layer, code)
            merged = _attach(merged, frame, layer.name, code, warns)
        for w in warns:
            if w not in self.warnings:
                self.warnings.append(w)
        return merged

    def daily(self, code: str, start: date, end: date) -> pd.DataFrame:
        merged = self._merged(code)
        today = date.today()
        recent = end >= today - timedelta(days=3)         # 「今の株価」を求める評価か(過去事例の計算は対象外)
        if recent:
            def stale(m: pd.DataFrame) -> bool:
                return m.empty or m["date"].max() < end - timedelta(days=self.max_stale_days)

            if stale(merged) and self.cfg is not None and code not in self._autofill_tried:
                self._autofill_tried.add(code)              # 1銘柄につき1回だけ、その場で空白補完を試す
                try:
                    fill_gap(self.cfg, [code], today=today)
                    merged = self._merged(code)
                except Exception as e:
                    self.warnings.append(f"{code}: 空白の自動補完に失敗({type(e).__name__})")
            if stale(merged):
                # 古い株価を「今の株価」として返さない(データ無し扱い。呼び出し側で除外される)
                last = merged["date"].max() if not merged.empty else None
                self.warnings.append(f"{code}: 株価が古い(最新 {last})ため、データ無しとして扱います")
                return pd.DataFrame(columns=COLUMNS)
        if merged.empty:
            return pd.DataFrame(columns=COLUMNS)
        return _normalize(merged, start, end)

    def status(self, today: date | None = None) -> list[dict]:
        """各層の鮮度(最新日付・経過日数・銘柄数)。読み取りのみ。"""
        import duckdb

        today = today or date.today()
        out = []
        for layer in self.layers:
            row = {"name": layer.name, "path": str(layer.path), "exists": layer.exists()}
            if layer.exists():
                con = duckdb.connect()
                try:
                    lo, hi, n, codes = con.execute(
                        "SELECT min(date), max(date), count(*), count(DISTINCT code) FROM read_parquet(?)",
                        [str(layer.path)]).fetchone()
                finally:
                    con.close()
                hi_d = pd.Timestamp(hi).date() if hi is not None else None
                row.update({"min_date": pd.Timestamp(lo).date() if lo is not None else None, "max_date": hi_d,
                            "rows": n, "codes": codes,
                            "stale_days": (today - hi_d).days if hi_d else None,
                            "file_mtime": datetime.fromtimestamp(layer.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
            out.append(row)
        return out


def layers_from_config(cfg) -> list[Layer]:
    """config の prices.layered から層を作る。連想側の空白補完ファイルは、データフォルダの中に置く。"""
    section = cfg.section("prices").get("layered") or {}
    layers: list[Layer] = []
    if section.get("tactical"):
        layers.append(Layer("既存・日次555銘柄", Path(section["tactical"])))
    layers.append(Layer("連想側・空白補完", gap_path(cfg)))
    if section.get("history"):
        layers.append(Layer("既存・全銘柄履歴", Path(section["history"])))
    return layers


def gap_path(cfg) -> Path:
    return cfg.data_dir / "prices_gap" / "prices_gap.parquet"


# ---- 空白補完(連想側が取得する。既存システムには書き込まない) ----------------------------------

def _symbol(code: str) -> str:
    return f"{code}.T" if code.isdigit() or (len(code) == 4 and code[:3].isdigit()) else code


def history_last_date(cfg) -> date | None:
    """既存の全銘柄履歴の最新日付(空白補完の開始点の目安)。"""
    section = cfg.section("prices").get("layered") or {}
    path = section.get("history")
    if not path or not Path(path).exists():
        return None
    import duckdb

    con = duckdb.connect()
    try:
        hi = con.execute("SELECT max(date) FROM read_parquet(?)", [str(path)]).fetchone()[0]
    finally:
        con.close()
    return pd.Timestamp(hi).date() if hi is not None else None


def _download_chunk(symbols: list[str], start: date, end: date, retries: int = 4) -> pd.DataFrame:
    import yfinance as yf

    for attempt in range(retries):
        try:
            df = yf.download(symbols, start=start, end=end + timedelta(days=1), progress=False,
                             auto_adjust=False, group_by="ticker", threads=False)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass
        time.sleep(5 * (2 ** attempt))                  # レート制限には指数バックオフ
    return pd.DataFrame()


def _split_chunk(df: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        try:
            sub = df[s] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.dropna(how="all")
        if sub.empty:
            continue
        sub = sub.reset_index().rename(columns=str.lower)
        if not set(COLUMNS) <= set(sub.columns):
            continue
        out[s] = sub[COLUMNS]
    return out


def fill_gap(cfg, codes: list[str], *, today: date, start: date | None = None,
             chunk: int = 50, pause: float = 3.0) -> dict:
    """codes の株価を start〜today で取得し、連想側の空白補完ファイルへ追記する。

    start の既定は「既存の全銘柄履歴の最新日 − 10日」。**重なる日を作る**ことで、つなぎ目の倍率を
    そろえられるようにする(yfinance は調整なしで取得するため、既存側と倍率が違うことがある)。
    既存システムのファイルには書き込まない。
    """
    import duckdb

    if start is None:
        last = history_last_date(cfg)
        start = (last - timedelta(days=10)) if last else today - timedelta(days=60)
    path = gap_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    ok_codes, empty_codes = [], []
    codes = list(dict.fromkeys(codes))
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        symbols = [_symbol(c) for c in part]
        by_symbol = _split_chunk(_download_chunk(symbols, start, today), symbols)
        for c, s in zip(part, symbols):
            df = by_symbol.get(s)
            if df is None or df.empty:
                empty_codes.append(c)
                continue
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df["code"] = c
            frames.append(df[["code"] + COLUMNS])
            ok_codes.append(c)
        if i + chunk < len(codes):
            time.sleep(pause)

    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["code"] + COLUMNS)
    con = duckdb.connect()
    try:
        con.register("new_rows", new)
        if path.exists():
            con.execute("CREATE TABLE cur AS SELECT * FROM read_parquet(?)", [str(path)])
            con.execute("""CREATE TABLE merged AS
                           SELECT * FROM (
                             SELECT code, CAST(date AS DATE) AS date, open, high, low, close, volume, 1 AS pri FROM new_rows
                             UNION ALL
                             SELECT code, CAST(date AS DATE), open, high, low, close, volume, 0 FROM cur)
                           QUALIFY row_number() OVER (PARTITION BY code, date ORDER BY pri DESC) = 1""")
        else:
            con.execute("""CREATE TABLE merged AS
                           SELECT code, CAST(date AS DATE) AS date, open, high, low, close, volume FROM new_rows""")
        tmp = path.with_suffix(".tmp.parquet")
        con.execute("COPY (SELECT code, date, open, high, low, close, volume FROM merged ORDER BY code, date) "
                    "TO ? (FORMAT PARQUET)".replace("?", f"'{tmp.as_posix()}'"))
        total = con.execute("SELECT count(*), count(DISTINCT code) FROM merged").fetchone()
    finally:
        con.close()
    tmp.replace(path)                                   # 途中で失敗しても、元のファイルが壊れないように置き換える
    return {"start": start.isoformat(), "requested": len(codes), "ok": len(ok_codes), "empty": empty_codes,
            "rows_downloaded": int(len(new)), "file_rows": int(total[0]), "file_codes": int(total[1]),
            "path": str(path)}
