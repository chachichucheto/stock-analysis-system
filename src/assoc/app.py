"""コマンドから呼ぶ処理のまとまり。部品をつなぎ、記録と DB への書き込みはここで行う。"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from assoc.config import REPO_ROOT, Config
from assoc.market.prices import YFinanceLivePriceSource, price_source_from_config
from assoc.pipeline.evaluate import CandidateEval, PriceLoader, evaluate
from assoc.report.daily import DayContext, write_daily
from assoc.scoring.end_rules import EndRuleInput, check_end
from assoc.scoring.scenario_cap import ScenarioForCap, scenarios_to_dormant
from assoc.state import load_scenarios, latest_by
from assoc.store import db
from assoc.store.records import KINDS, RecordStore
from assoc.timeutil import JST, add_business_days, business_days_between, is_business_day, jst_date, to_iso, utcnow
from assoc.tracking.grade_check import check_grade
from assoc.tracking.moved import check_moved
from assoc.tracking.sell_signals import SellSignalInput, sell_signals


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.th = cfg.thresholds
        self.data = cfg.data_dir
        self.store = RecordStore(self.data / "records")
        self._con = None

    @property
    def con(self):
        if self._con is None:
            self._con = db.connect(self.data)
        return self._con

    def dir(self, name: str) -> Path:
        p = self.data / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def today(self) -> date:
        return jst_date(utcnow())

    def topix_code(self) -> str:
        return str(self.cfg.section("prices").get("topix_code", "1306"))

    # ---- 収集 -------------------------------------------------------------
    def collect(self, sources: list[str] | None = None) -> dict[str, Any]:
        from assoc.ingest.run import collect
        return collect(self.cfg, self.con, sources=sources) if _accepts_con(collect) else collect(self.cfg, sources=sources)

    # ---- 夕方:イベント化・足切り・入力パック ------------------------------------
    def prepare(self, day: date | None = None) -> Path:
        from assoc.pack.build import build_pack
        day = day or self.today()
        return build_pack(self, day)

    # ---- 夜:確定 -----------------------------------------------------------
    def commit(self, path: Path | None = None):
        from assoc.commit.daily import commit_file
        from assoc.master.names import CompanyMaster
        inbox = self.dir("inbox")
        if path is None:
            files = sorted(inbox.glob("daily_*.json"))
            if not files:
                raise FileNotFoundError(f"確定する出力がありません: {inbox}")
            path = files[-1]
        master = CompanyMaster.from_db(self.con, REPO_ROOT / "knowledge" / "aliases.yaml")
        return commit_file(path, self.store, master, self.th)

    # ---- 夜:ランキングとレポート -----------------------------------------------
    def evaluate(self, day: date | None = None) -> list[CandidateEval]:
        day = day or self.today()
        scenarios = load_scenarios(self.store)
        loader = PriceLoader(price_source_from_config(self.cfg), day, YFinanceLivePriceSource())
        gates = latest_by(self.store, "event_gate", "event_id")
        # 注目度の履歴が足りず判定できなかったイベントは、LLM が S・A級と判定したものを「高い」とみなす
        grade_of = {g["event_id"]: g["provisional_grade"] for g in self.store.read("grade")}
        grade_of.update({f["event_id"]: f["final_grade"] for f in self.store.read("grade_final")})
        attention_high = {k: v["attention_high"] if v.get("attention_high") is not None
                          else grade_of.get(k) in ("S", "A") for k, v in gates.items()}
        temps = {k: v["temperature"] for k, v in latest_by(self.store, "theme_temperature", "theme_id").items()}
        initial = {r["scenario_id"]: r["strength"] for r in self.store.read("scenario")}
        low_tier = self._low_tier_only()
        kanri = {r[0] for r in self.con.execute(
            "SELECT code FROM universe_daily WHERE date = (SELECT max(date) FROM universe_daily) "
            "AND status IN ('監理', '整理')").fetchall()}
        calendar = {r[0]: {"earnings_date": r[1], "ex_rights_date": r[2], "credit_restriction": r[3]}
                    for r in self.con.execute(
                        "SELECT code, earnings_date, ex_rights_date, credit_restriction FROM calendar").fetchall()}
        evals = evaluate(scenarios, loader, self.topix_code(), day, self.th, attention_high=attention_high,
                         temperatures=temps, initial_strength=initial, low_tier_only=low_tier, kanri=kanri,
                         calendar=calendar)
        self._copy_prices(loader)
        return evals

    def _low_tier_only(self) -> set[str]:
        tiers: dict[str, list[int]] = {}
        for ev in self.store.read("evidence"):
            if ev.get("supports") == "for":
                tiers.setdefault(ev["scenario_id"], []).append(ev["source_tier"])
        return {sid for sid, ts in tiers.items() if ts and min(ts) >= 3}

    def _copy_prices(self, loader: PriceLoader) -> None:
        """候補に使った銘柄の株価を複製する(上場廃止すると yfinance から消えるため。CONCEPT §14.1)。"""
        for code, df in loader.loaded().items():
            if df.empty:
                continue
            rows = df.assign(code=code)[["code", "date", "open", "high", "low", "close", "volume"]]
            self.con.register("_prices", rows)
            self.con.execute("INSERT OR REPLACE INTO price_copy SELECT * FROM _prices")
            self.con.unregister("_prices")

    def report(self, day: date | None = None) -> tuple[Path, Path]:
        day = day or self.today()
        evals = self.evaluate(day)
        picked_before = {(r["scenario_id"], r["code"]) for r in self.store.read("pick_tracking")}
        for e in evals:
            self.store.append("ranking_snapshot", {
                "date": day.isoformat(), "scenario_id": e.scenario.scenario_id,
                "candidate_id": e.candidate.get("candidate_id"), "code": e.candidate["code"],
                "rank": e.rank, "tier": e.tier, "confidence": e.confidence, "stars": e.stars,
                "realization_prob": e.realization_prob, "remaining_room": e.remaining_room,
                "freshness": e.freshness, "priced_in": e.priced_in, "diagnosis": e.diagnosis,
                "excluded_reason": e.excluded_reason, "stage": e.candidate.get("stage")})
            key = (e.scenario.scenario_id, e.candidate["code"])
            if e.tier == "本命" and key not in picked_before:
                # (シナリオ, 銘柄) の組で最初に本命になった日だけを1件と数える(DESIGN §7)
                self.store.append("pick_tracking", {
                    "scenario_id": key[0], "code": key[1], "candidate_id": e.candidate.get("candidate_id"),
                    "stage": e.candidate.get("stage"), "first_pick_date": day.isoformat(),
                    "expected_days": e.scenario.expected_days, "event": "first_pick"})
        ctx = self._day_context(day)
        return write_daily(self.dir("reports"), self.dir("export"), day, evals, ctx)

    def _day_context(self, day: date) -> DayContext:
        scenarios = load_scenarios(self.store)
        iso = day.isoformat()
        grades = {g["event_id"]: dict(g) for g in self.store.read("grade")}
        for f in self.store.read("grade_final"):
            grades.setdefault(f["event_id"], {}).update(final_grade=f["final_grade"])
        evidence: dict[str, list] = {}
        for ev in self.store.read("evidence"):
            evidence.setdefault(ev["scenario_id"], []).append(ev)
        ended = [(scenarios[s["scenario_id"]], s.get("reason", "")) for s in self.store.read("scenario_status")
                 if s.get("date") == iso and s["status"] == "終了" and s["scenario_id"] in scenarios]
        signals = [(s["code"], s.get("company_name", ""), s["signals"]) for s in self.store.read("pick_tracking")
                   if s.get("event") == "sell_signal" and s.get("date") == iso]
        warnings = []
        try:
            from assoc.ingest.run import sources_with_consecutive_failures
            failing = sources_with_consecutive_failures(self.con)
            if failing:
                warnings.append(f"収集が3回続けて失敗しています: {', '.join(failing)}")
        except Exception:
            pass
        return DayContext(
            new_scenarios=[s for s in scenarios.values() if s.created_date == day],
            updates=[u for u in self.store.read("scenario_update") if u.get("run_date") == iso],
            ended=ended, grades=grades, evidence=evidence, sell_signals=signals, warnings=warnings)

    # ---- 翌日の引け後:等級の確定・状態の更新・売りのサイン・「動いた」 ------------------
    def nextday(self, day: date | None = None) -> dict[str, int]:
        day = day or self.today()
        counts = {"grade_final": 0, "ended": 0, "dormant": 0, "moved": 0, "sell_signal": 0}
        loader = PriceLoader(price_source_from_config(self.cfg), day, YFinanceLivePriceSource())
        topix = loader.get(self.topix_code())

        # 等級の確定:前営業日以前に付けた暫定の等級のうち、まだ確定していないもの
        finalized = {f["event_id"] for f in self.store.read("grade_final")}
        for g in self.store.read("grade"):
            gdate = date.fromisoformat(g["run_date"])
            if g["event_id"] in finalized or gdate >= day:
                continue
            check_day = add_business_days(gdate, 1)
            if check_day > day:
                continue
            prices = {c: p for c in g.get("check_codes", []) if not (p := loader.get(c)).empty}
            # ザラ場中のニュースは当日に、引け後のニュースは翌営業日に反応する。反応の大きいほうで判定する
            results = [check_grade(g["provisional_grade"], g["check_basis"], g.get("check_codes", []), d,
                                   prices, topix, self.th.vol_window, self.th) for d in (gdate, check_day)]
            res = max(results, key=lambda x: -1 if x.max_sigma is None else x.max_sigma)
            self.store.append("grade_final", {"event_id": g["event_id"], "provisional_grade": g["provisional_grade"],
                                              "final_grade": res.final_grade, "reacted": res.reacted,
                                              "max_sigma": res.max_sigma, "note": res.note,
                                              "check_date": check_day.isoformat()})
            finalized.add(g["event_id"])
            counts["grade_final"] += 1

        # シナリオの終了判定と休眠
        evals = self.evaluate(day)
        scenarios = load_scenarios(self.store)
        by_scenario: dict[str, list[CandidateEval]] = {}
        for e in evals:
            by_scenario.setdefault(e.scenario.scenario_id, []).append(e)
        for sid, sc in scenarios.items():
            if not sc.is_active:
                continue
            es = by_scenario.get(sid, [])
            reason = check_end(EndRuleInput(
                started=sc.status == "進行中", start_date=sc.clock_date, created_date=sc.created_date,
                eval_date=day, expected_days=sc.expected_days,
                tier1_reacted=any(e.tier1_reacted for e in es),
                candidate_reacted=any(e.candidate_reacted and e.candidate.get("stage", 2) >= 2 for e in es),
                priced_in_value=max((e.priced_in for e in es), default=0.0),
                end_condition_triggered=False), self.th)
            if reason:
                self.store.append("scenario_status", {"scenario_id": sid, "status": "終了", "reason": reason,
                                                      "date": day.isoformat()})
                counts["ended"] += 1
        active = [ScenarioForCap(sid, max((e.confidence for e in by_scenario.get(sid, [])), default=0.0))
                  for sid, sc in load_scenarios(self.store).items() if sc.is_active]
        for s in scenarios_to_dormant(active, self.th):
            self.store.append("scenario_status", {"scenario_id": s.scenario_id, "status": "休眠",
                                                  "reason": "シナリオの上限を超えたため", "date": day.isoformat()})
            counts["dormant"] += 1

        # 過去に本命とした銘柄:「動いた」の判定と売りのサイン
        scenarios = load_scenarios(self.store)
        eval_by_key = {(e.scenario.scenario_id, e.candidate["code"]): e for e in evals}
        picks = [p for p in self.store.read("pick_tracking") if p.get("event") == "first_pick"]
        already_moved = {(p["scenario_id"], p["code"]) for p in self.store.read("pick_tracking") if p.get("event") == "moved"}
        last_signals = {(p["scenario_id"], p["code"]): p["signals"] for p in self.store.read("pick_tracking")
                        if p.get("event") == "sell_signal"}
        for p in picks:
            key = (p["scenario_id"], p["code"])
            first = date.fromisoformat(p["first_pick_date"])
            prices = loader.get(p["code"])
            if key not in already_moved and not prices.empty and not topix.empty and first < day:
                m = check_moved(prices, topix, first, p["expected_days"], self.th)
                if m.moved:
                    self.store.append("pick_tracking", {"scenario_id": key[0], "code": key[1], "event": "moved",
                                                        "moved_at": m.moved_at.isoformat() if m.moved_at else None,
                                                        "days_to_move": m.days_to_move, "max_excess": m.max_excess,
                                                        "date": day.isoformat()})
                    counts["moved"] += 1
            sc = scenarios.get(key[0])
            if sc is None:
                continue
            e = eval_by_key.get(key)
            weakened = sc.status == "終了" or any(u["type"] in ("弱体化", "消滅") for u in sc.updates)
            remaining = e.remaining_days if e and e.remaining_days is not None else 99
            sig = sell_signals(SellSignalInput(break_condition_triggered=False, scenario_weakened_or_dead=weakened,
                                               priced_in_value=e.priced_in if e else 0.0, remaining_days=remaining),
                               self.th)
            # 同じサインを毎日繰り返さない。サインの中身が変わったときだけ記録する
            if sig and sig != last_signals.get(key) and business_days_between(first, day) <= 60:
                name = next((c.get("master_name") or c["company_name"] for c in sc.candidates if c["code"] == key[1]), "")
                self.store.append("pick_tracking", {"scenario_id": key[0], "code": key[1], "company_name": name,
                                                    "event": "sell_signal", "signals": sig, "date": day.isoformat()})
                counts["sell_signal"] += 1
        return counts

    # ---- 翌朝:米国の1段目 -------------------------------------------------------
    def morning(self, day: date | None = None) -> list[str]:
        """前夜の等級の検算対象に米国株があれば、米国市場の反応を確認して通知文を返す。"""
        day = day or self.today()
        prev = add_business_days(day, -1)
        notices = []
        live = YFinanceLivePriceSource()
        for g in self.store.read("grade"):
            if g.get("run_date") != prev.isoformat():
                continue
            for code in g.get("check_codes", []):
                if code[:1].isdigit():
                    continue
                df = live.daily(code, day - timedelta(days=60), day)
                if len(df) < self.th.vol_window + 2:
                    continue
                r = df["close"].pct_change()
                sigma = r.iloc[:-1].tail(self.th.vol_window).std()
                if sigma and abs(r.iloc[-1]) >= self.th.reacted_sigma * sigma:
                    notices.append(f"{g['event_id']}: 米国の1段目 {code} が {r.iloc[-1]:+.1%}(普段の {abs(r.iloc[-1]) / sigma:.1f} 倍)")
        path = self.dir("reports") / f"morning_{day.isoformat()}.md"
        path.write_text("# 翌朝の確認\n\n" + ("\n".join(f"- {n}" for n in notices) or "- 大きな反応なし") + "\n",
                        encoding="utf-8")
        return notices

    # ---- 保守 --------------------------------------------------------------
    def verify(self) -> list[str]:
        problems = []
        for kind in sorted(KINDS):
            problems += self.store.verify(kind)
        return problems

    def backup(self) -> Path | None:
        target = self.cfg.path("backup_dir")
        if not str(target) or str(target) == ".":
            return None
        dest = Path(target) / f"records_{self.today().isoformat()}"
        shutil.copytree(self.store.dir, dest, dirs_exist_ok=True)
        return dest


def _accepts_con(fn) -> bool:
    import inspect
    return "con" in inspect.signature(fn).parameters


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
