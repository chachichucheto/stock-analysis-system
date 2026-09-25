"""追記専用の記録(docs/DESIGN.md §4)。

記録の種類ごとに data/records/<kind>.jsonl に1行ずつ追記する。各行は直前の行のハッシュを
含むので、途中の行が書き換えられると verify() で検出できる。書き換えや削除の手段は用意しない。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from assoc.timeutil import to_iso, utcnow

GENESIS = "0" * 64

# 作り直せない記録の種類(DESIGN §5)。ここにない種類は受け付けない。
KINDS = frozenset({
    "grade", "premise_card", "scenario", "scenario_update", "check_plan",
    "evidence", "candidate", "ranking_snapshot", "pick_tracking", "user_decision",
    "run_log", "event_gate", "commit_rejection", "theme_temperature", "monthly_review",
    "scenario_status", "grade_final", "past_case",
})


class RecordStore:
    def __init__(self, records_dir: Path):
        self.dir = Path(records_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _file(self, kind: str) -> Path:
        if kind not in KINDS:
            raise ValueError(f"未知の記録の種類です: {kind}")
        return self.dir / f"{kind}.jsonl"

    def _last_hash(self, path: Path) -> tuple[int, str]:
        if not path.exists() or path.stat().st_size == 0:
            return 0, GENESIS
        with path.open("rb") as f:
            f.seek(0, 2)
            pos = f.tell() - 1
            while pos > 0:
                f.seek(pos - 1)
                if f.read(1) == b"\n":
                    break
                pos -= 1
            f.seek(max(pos, 0))
            last = json.loads(f.readline().decode("utf-8"))
        return last["_seq"], last["_hash"]

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """1件追記して、付与した項目を含む行を返す。"""
        path = self._file(kind)
        seq, prev = self._last_hash(path)
        row = {"_seq": seq + 1, "_kind": kind, "_recorded_at": to_iso(utcnow()),
               "_prev_hash": prev, **payload}
        row["_hash"] = _hash_row(row)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return row

    def read(self, kind: str) -> Iterator[dict[str, Any]]:
        path = self._file(kind)
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def verify(self, kind: str) -> list[str]:
        """ハッシュの連鎖を検証し、問題の一覧を返す(空なら正常)。"""
        problems, prev, expected_seq = [], GENESIS, 1
        for row in self.read(kind):
            if row.get("_seq") != expected_seq:
                problems.append(f"{kind}: 連番の飛び(期待 {expected_seq}、実際 {row.get('_seq')})")
            if row.get("_prev_hash") != prev:
                problems.append(f"{kind}#{row.get('_seq')}: 直前の行とつながっていない")
            if _hash_row(row) != row.get("_hash"):
                problems.append(f"{kind}#{row.get('_seq')}: 内容が書き換えられている")
            prev, expected_seq = row.get("_hash"), expected_seq + 1
        return problems


def _hash_row(row: dict[str, Any]) -> str:
    body = {k: v for k, v in row.items() if k != "_hash"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
