"""追記専用の記録から、今のシナリオの状態を組み立てる。

記録は書き換えないので、「今の状態」は毎回ここで作り直す。
- scenario:立てたときの内容(版1)
- scenario_update:LLM が判定した強化・弱体化・繰り返し・消滅・起動
- scenario_status:Python が決めた状態の変化(休眠、終了とその理由)
- check_plan:確認計画と、その後の確認状況(status_update=True の行)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from assoc.store.records import RecordStore

ACTIVE = ("進行中", "待機")


@dataclass
class ScenarioState:
    scenario_id: str
    statement: str
    event_id: str
    theme_ids: list[str]
    time_type: str
    status: str                      # 待機 | 進行中 | 休眠 | 終了
    strength: str
    expected_days: int
    expected_rise: dict[str, Any]    # low, median, high, basis, version
    created_date: date
    started_date: date | None        # 起動日(待機中は None)
    clock_date: date | None          # 鮮度・想定期間の起点(起動日、または直近の強化日)
    end_condition: str
    phase: str | None = None
    start_condition: str | None = None
    new_theme_flag: bool = False
    end_reason: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    check_plan: dict[int, dict[str, Any]] = field(default_factory=dict)
    updates: list[dict[str, Any]] = field(default_factory=list)
    initial_expected_rise: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE

    @property
    def verified_ratio(self) -> float:
        if not self.check_plan:
            return 0.0
        return sum(1 for p in self.check_plan.values() if p.get("status") == "確認") / len(self.check_plan)

    @property
    def denied_arrows(self) -> int:
        return sum(1 for p in self.check_plan.values() if p.get("status") == "否定")


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def load_scenarios(store: RecordStore) -> dict[str, ScenarioState]:
    scenarios: dict[str, ScenarioState] = {}
    for r in store.read("scenario"):
        created = _d(r["run_date"])
        started = created if r.get("started") else None
        er = dict(r["expected_rise"])
        scenarios[r["scenario_id"]] = ScenarioState(
            scenario_id=r["scenario_id"], statement=r["statement"], event_id=r["event_id"],
            theme_ids=r.get("theme_ids", []), time_type=r["time_type"], status=r["status"],
            strength=r["strength"], expected_days=r["expected_days"], expected_rise=er,
            initial_expected_rise=dict(er), created_date=created, started_date=started,
            clock_date=started, end_condition=r["end_condition"], phase=r.get("phase"),
            start_condition=r.get("start_condition"), new_theme_flag=r.get("new_theme_flag", False))

    for c in store.read("candidate"):
        if c["scenario_id"] in scenarios:
            scenarios[c["scenario_id"]].candidates.append(c)

    for p in store.read("check_plan"):
        sc = scenarios.get(p["scenario_id"])
        if sc is None:
            continue
        if p.get("status_update"):
            if p["arrow_no"] in sc.check_plan:
                sc.check_plan[p["arrow_no"]]["status"] = p["status"]
        else:
            sc.check_plan[p["arrow_no"]] = dict(p)

    for u in store.read("scenario_update"):
        sc = scenarios.get(u["scenario_id"])
        if sc is None:
            continue
        day = _d(u["run_date"])
        sc.updates.append(u)
        sc.strength = u.get("strength_after", sc.strength)
        if u.get("expected_rise"):
            sc.expected_rise = {**u["expected_rise"], "version": sc.expected_rise.get("version", 1) + 1}
        if u["type"] == "強化" and sc.status == "進行中":
            sc.clock_date = day                         # 鮮度と想定期間はここから数え直す(CONCEPT §8.1)
        elif u["type"] == "起動" and sc.status == "待機":
            sc.status, sc.started_date, sc.clock_date = "進行中", day, day
        elif u["type"] == "消滅":
            sc.status, sc.end_reason = "終了", "シナリオが崩れた"

    for s in store.read("scenario_status"):
        sc = scenarios.get(s["scenario_id"])
        if sc is None:
            continue
        sc.status = s["status"]
        sc.end_reason = s.get("reason", sc.end_reason)
        if s["status"] == "進行中" and sc.started_date is None:
            sc.started_date = sc.clock_date = _d(s["date"])
    return scenarios


def latest_by(store: RecordStore, kind: str, key: str) -> dict[str, dict[str, Any]]:
    """同じ key の最新の記録を返す(前提カードやテーマの温度など)。"""
    out: dict[str, dict[str, Any]] = {}
    for r in store.read(kind):
        out[r[key]] = r
    return out
