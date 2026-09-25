"""夜の手順の実行記録(docs/DESIGN.md §9.2)。所要時間と、実行しなかった理由を残す。"""
from __future__ import annotations

import json
from pathlib import Path

from assoc.store.records import RecordStore
from assoc.timeutil import jst_date, parse_iso, to_iso, utcnow


def _marker(data_dir: Path) -> Path:
    return Path(data_dir) / "session_started.json"


def start(data_dir: Path) -> str:
    started = to_iso(utcnow())
    _marker(data_dir).write_text(json.dumps({"started_at": started}), encoding="utf-8")
    return started


def end(data_dir: Path, store: RecordStore, mode: str, model_id: str = "", prompt_version: str = "daily-v1") -> dict:
    marker = _marker(data_dir)
    now = utcnow()
    duration = None
    if marker.exists():
        started = parse_iso(json.loads(marker.read_text(encoding="utf-8"))["started_at"])
        duration = round((now - started).total_seconds() / 60, 1)
        marker.unlink()
    return store.append("run_log", {"date": jst_date(now).isoformat(), "executed": True, "mode": mode,
                                    "duration_min": duration, "skip_reason": None,
                                    "model_id": model_id, "prompt_version": prompt_version})


def skip(store: RecordStore, reason: str) -> dict:
    return store.append("run_log", {"date": jst_date(utcnow()).isoformat(), "executed": False, "mode": None,
                                    "duration_min": None, "skip_reason": reason,
                                    "model_id": None, "prompt_version": None})
