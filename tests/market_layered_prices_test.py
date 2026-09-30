from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from assoc.market import layered_prices as lp


def _write_parquet(path: Path, rows: list[tuple]) -> None:
    df = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    con = duckdb.connect()
    con.register("df", df)
    con.execute(f"COPY df TO '{path.as_posix()}' (FORMAT PARQUET)")
    con.close()


def _series(code, start: date, n: int, price: float, step: float = 1.0):
    out = []
    d = start
    for i in range(n):
        p = price + i * step
        out.append((code, d, p, p, p, p, 1000.0))
        d += timedelta(days=1)
    return out


@pytest.fixture(autouse=True)
def _no_settle_wait(monkeypatch):
    monkeypatch.setattr(lp, "SETTLE_SECONDS", 0)


def test_quality_gate_drops_broken_rows():
    df = pd.DataFrame({"date": [date(2026, 1, i) for i in range(1, 5)],
                       "open": [1, 1, 1, 1], "high": [1, 1, 1, 1], "low": [1, 1, 1, 1],
                       "close": [100.0, 1.68e9, 0.0, None], "volume": [1, 1, 1, 1]})
    out, dropped = lp.quality_gate(df)
    assert len(out) == 1 and out["close"].iloc[0] == 100.0 and dropped == 3


def test_split_ratio_is_aligned_at_the_seam(tmp_path):
    # 新しい層は、分割後の(3分の1の)価格。古い層は分割前の価格。重なる日の比で古い側をそろえる。
    new_p, old_p = tmp_path / "new.parquet", tmp_path / "old.parquet"
    _write_parquet(new_p, _series("285A", date(2026, 7, 10), 10, 100.0))
    _write_parquet(old_p, _series("285A", date(2026, 6, 1), 50, 270.0, step=3.0))   # 7/10〜7/20 が新しい層と重なる(約3.9倍)
    src = lp.LayeredPriceSource([lp.Layer("new", new_p), lp.Layer("old", old_p)])
    df = src.daily("285A", date(2026, 6, 1), date(2026, 7, 31))
    closes = df.set_index("date")["close"]
    # つなぎ目(7/9 → 7/10)で、偽のジャンプが無い
    jump = closes.loc[date(2026, 7, 10)] / closes.loc[date(2026, 7, 9)]
    assert 0.9 < jump < 1.1
    assert not src.warnings


def test_one_day_overlap_is_still_aligned(tmp_path):
    new_p, old_p = tmp_path / "new.parquet", tmp_path / "old.parquet"
    _write_parquet(new_p, _series("6666", date(2026, 7, 10), 5, 100.0))
    _write_parquet(old_p, _series("6666", date(2026, 6, 1), 40, 380.0, step=0.0))    # 7/10 だけ重なる。約3.8倍
    src = lp.LayeredPriceSource([lp.Layer("new", new_p), lp.Layer("old", old_p)])
    df = src.daily("6666", date(2026, 6, 1), date(2026, 7, 31))
    closes = df.set_index("date")["close"]
    assert 0.9 < closes.loc[date(2026, 7, 10)] / closes.loc[date(2026, 7, 9)] < 1.1


def test_unstable_ratio_rejects_old_layer_with_warning(tmp_path):
    new_p, old_p = tmp_path / "new.parquet", tmp_path / "old.parquet"
    _write_parquet(new_p, _series("1111", date(2026, 7, 10), 10, 100.0))
    # 重なる期間の途中で倍率が変わる(分割が重なりの途中にある)古い層
    rows = _series("1111", date(2026, 7, 10), 5, 100.0) + _series("1111", date(2026, 7, 15), 5, 300.0)
    rows = [(c, d, o, h, l, cl, v) for (c, d, o, h, l, cl, v) in rows]
    _write_parquet(old_p, rows)
    src = lp.LayeredPriceSource([lp.Layer("new", new_p), lp.Layer("old", old_p)])
    df = src.daily("1111", date(2026, 7, 1), date(2026, 7, 31))
    assert df["date"].min() == date(2026, 7, 10)          # 古い層は使わない
    assert any("安定せず" in w for w in src.warnings)


def test_seam_jump_without_overlap_rejects_old_layer(tmp_path):
    new_p, old_p = tmp_path / "new.parquet", tmp_path / "old.parquet"
    _write_parquet(new_p, _series("2222", date(2026, 7, 10), 5, 100.0))
    _write_parquet(old_p, _series("2222", date(2026, 6, 1), 20, 1000.0))     # 重ならず、つなぎ目で10分の1
    src = lp.LayeredPriceSource([lp.Layer("new", new_p), lp.Layer("old", old_p)])
    df = src.daily("2222", date(2026, 6, 1), date(2026, 7, 31))
    assert df["date"].min() == date(2026, 7, 10)
    assert any("つなぎ目" in w for w in src.warnings)


def test_no_overlap_small_seam_is_accepted(tmp_path):
    new_p, old_p = tmp_path / "new.parquet", tmp_path / "old.parquet"
    _write_parquet(new_p, _series("3333", date(2026, 7, 10), 5, 105.0))
    _write_parquet(old_p, _series("3333", date(2026, 6, 20), 20, 100.0, step=0.2))   # 7/9 の終値 ≈ 103.8
    src = lp.LayeredPriceSource([lp.Layer("new", new_p), lp.Layer("old", old_p)])
    df = src.daily("3333", date(2026, 6, 1), date(2026, 7, 31))
    assert df["date"].min() == date(2026, 6, 20) and not src.warnings


def test_stale_recent_prices_are_not_returned(tmp_path):
    today = date.today()
    old_p = tmp_path / "old.parquet"
    _write_parquet(old_p, _series("4444", today - timedelta(days=80), 10, 100.0))   # 70日以上前で終わる
    src = lp.LayeredPriceSource([lp.Layer("old", old_p)], cfg=None)
    df = src.daily("4444", today - timedelta(days=200), today)
    assert df.empty
    assert any("古い" in w for w in src.warnings)
    # 過去の日付を終点にする評価(過去事例の計算)では、古さを理由に捨てない
    past = src.daily("4444", today - timedelta(days=200), today - timedelta(days=60))
    assert not past.empty


def test_missing_layer_file_is_skipped(tmp_path):
    p = tmp_path / "a.parquet"
    _write_parquet(p, _series("5555", date(2026, 7, 1), 5, 100.0))
    src = lp.LayeredPriceSource([lp.Layer("none", tmp_path / "none.parquet"), lp.Layer("a", p)])
    assert len(src.daily("5555", date(2026, 7, 1), date(2026, 7, 31))) == 5
