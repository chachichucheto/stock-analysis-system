"""割安カタリスト・モデルの手順(docs/VALUE_DESIGN.md §7)。部品をつなぎ、ファイルと記録への書き込みはここで行う。

  screen   機械の部分:スクリーン → LLM に渡す入力パック(data/value/pack_YYYY-MM-DD.json)
  (LLM)    prompts/value-disclosure-v1.md に従って開示を読み、data/value/inbox/value_YYYY-MM-DD.json を作る
  commit   出力の検証(引用が開示の文面にあるか等)と記録への追記
  report   合議スコアでランキングし、レポート(data/reports/value_YYYY-MM-DD.md)と連携ファイルを作る
  backtest 過去検証
  eval     開示読解プロンプトの評価
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from assoc.config import REPO_ROOT
from assoc.market.prices import PriceSource, price_source_from_config
from assoc.value import backtest as bt
from assoc.value import catalyst as cat
from assoc.value import evalset, risk
from assoc.value.dangers import detect_dangers
from assoc.value.data import FinancialsStore, Universe
from assoc.value.score import Candidate, Catalyst, evaluate_candidate, prelist_for_llm, rank
from assoc.value.screens import run_screens
from assoc.value.snapshot import build_snapshot, passes_base
from assoc.value.thresholds import ValueThresholds, thresholds_from_config

if TYPE_CHECKING:
    from assoc.app import App

PACK_VERSION = 1
TYPE_NAMES = {"A": "資産バリュー", "B": "シクリカル底", "C": "成長の初動", "D": "売られすぎ反発(補助)"}


@dataclass
class ValueEnv:
    app: "App"
    th: ValueThresholds
    dir: Path
    universe: Universe
    fin: FinancialsStore
    prices: PriceSource
    capital: float | None


def load_env(app: "App") -> ValueEnv:
    section = app.cfg.section("value")
    base = Path(section.get("dir") or app.data / "value")
    if not base.is_absolute():
        base = REPO_ROOT / base
    uni, fin = base / "universe.csv", base / "financials.csv"
    for p, what in ((uni, "銘柄マスタ"), (fin, "財務データ")):
        if not p.exists():
            raise FileNotFoundError(f"{what}がありません: {p}(形式は docs/VALUE_DESIGN.md §5)")
    cap = section.get("capital_yen")
    return ValueEnv(app=app, th=thresholds_from_config(app.cfg), dir=base, universe=Universe.from_csv(uni),
                    fin=FinancialsStore.from_csv(fin), prices=price_source_from_config(app.cfg),
                    capital=float(cap) if cap else None)


# ---- 機械の部分 -------------------------------------------------------------------

def compute_candidates(env: ValueEnv, asof: date) -> list[Candidate]:
    """足切りを通った銘柄のうち、型が1つでも該当したもの(除外された銘柄も、理由を見せるため含める)。"""
    out = []
    start = asof - timedelta(days=1900)
    for u in env.universe.members(asof):
        df = env.prices.daily(u.code, start, asof)
        if df.empty:
            continue
        snap = build_snapshot(u, asof, env.fin, df, env.th)
        if snap is None:
            continue
        ok, _ = passes_base(snap, env.th)
        if not ok:
            continue
        screens = run_screens(snap, env.th)
        if not any(s.hit for s in screens.values()):
            continue
        out.append(evaluate_candidate(snap, screens, detect_dangers(snap, env.th, master=u), None, env.th))
    return out


def _disclosures(app: "App", code: str, asof: date, days: int = 45) -> list[dict[str, Any]]:
    """収集済みの適時開示(タイトルと URL)。TDnet の銘柄コードは5桁(末尾0)のことがあるので両方に合わせる。
    本文(body_excerpt)は、ローカルで取得処理を足したときに入れる(docs/VALUE_LOCAL_KICKOFF.md フェーズD)。"""
    since = asof - timedelta(days=days)
    rows = app.con.execute(
        "SELECT disclosure_id, published_at, title, url FROM disclosure "
        "WHERE (code = ? OR code = ?) AND CAST(published_at AS DATE) > ? AND CAST(published_at AS DATE) <= ? "
        "ORDER BY published_at DESC", [code, code + "0", since, asof]).fetchall()
    return [{"id": r[0], "published_at": str(r[1]), "title": r[2], "url": r[3], "body_excerpt": ""} for r in rows]


def _fact(x: Any) -> Any:
    return None if x is None else (round(x, 4) if isinstance(x, float) else x)


def build_pack(env: ValueEnv, asof: date) -> Path:
    """その日の入力パックを作る。**すでにあれば作り直さない**(その日に LLM が読んだ内容と、確定時の照合元を変えないため)。
    データを直して作り直したいときは、パックのファイルを消してから実行する。"""
    path = env.dir / f"pack_{asof}.json"
    if path.exists():
        return path
    cands = compute_candidates(env, asof)
    pre = prelist_for_llm(cands, env.th)
    items = []
    for c in pre:
        items.append({"key": c.code, "name": c.name, "sector33": c.snapshot.sector33, "types_hit": c.types_hit,
                      "facts": {k: {kk: _fact(vv) for kk, vv in s.facts.items()} for k, s in c.screens.items() if s.hit},
                      "reasons": {k: s.reasons for k, s in c.screens.items() if s.hit},
                      "dangers": [{"severity": d.severity, "text": d.text} for d in c.dangers],
                      "disclosures": _disclosures(env.app, c.code, asof)})
    pack = {"version": PACK_VERSION, "asof": asof.isoformat(), "mode": "通常" if items else "該当なし",
            "universe_size": len(env.universe.members(asof)), "screened_hits": len(cands), "items": items}
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    # 後から「その日に何を候補にしたか」を数えられるよう、機械だけの候補を追記専用で残す(重複は書かない)
    done = {r["code"] for r in env.app.store.read("value_candidate") if r["asof"] == asof.isoformat()}
    for c in rank(cands):
        if c.code not in done:
            env.app.store.append("value_candidate", {"asof": asof.isoformat(), "code": c.code, "name": c.name,
                                                     "types": c.types_hit, "score": c.score, "price": c.snapshot.price,
                                                     "thresholds_sha": _th_sha(env.th)})
    return path


def _th_sha(th: ValueThresholds) -> str:
    return hashlib.sha256(repr(th).encode()).hexdigest()[:12]


# ---- LLM の出力の確定 ----------------------------------------------------------------

def _pack_sources(pack: dict) -> dict[str, dict[str, str]]:
    return {it["key"]: {d["id"]: f"{d['title']}\n{d.get('body_excerpt', '')}" for d in it["disclosures"]}
            for it in pack["items"]}


def commit(env: ValueEnv, path: Path | None = None) -> tuple[list[str], int]:
    inbox = env.dir / "inbox"
    if path is None:
        files = sorted(inbox.glob("value_*.json"))
        if not files:
            raise FileNotFoundError(f"確定する出力がありません: {inbox}")
        path = files[-1]
    out = cat.read_json(path)
    pack_path = env.dir / f"pack_{out.get('asof', '')}.json"
    if not pack_path.exists():
        return [f"対応する入力パックがありません: {pack_path.name}"], 0
    pack = cat.read_json(pack_path)
    errors = cat.validate_output(out, sources=_pack_sources(pack), allowed_keys={it["key"] for it in pack["items"]})
    if errors:
        return errors, 0
    for it in out["items"]:
        env.app.store.append("value_catalyst", {"asof": out["asof"], **it})
    return [], len(out["items"])


# ---- ランキングとレポート --------------------------------------------------------------

def _latest_catalysts(env: ValueEnv, asof: date) -> dict[str, Catalyst]:
    latest: dict[str, dict] = {}
    for r in env.app.store.read("value_catalyst"):
        if r["asof"] == asof.isoformat():
            latest[r["key"]] = r
    return cat.to_catalysts({"items": list(latest.values())})


def _attach(env: ValueEnv, cands: list[Candidate], catalysts: dict[str, Catalyst]) -> list[Candidate]:
    return [evaluate_candidate(c.snapshot, c.screens, c.dangers, catalysts.get(c.code), env.th) for c in cands]


def report(env: ValueEnv, asof: date) -> tuple[Path, Path]:
    th = env.th
    catalysts = _latest_catalysts(env, asof)
    cands = _attach(env, compute_candidates(env, asof), catalysts)
    ranked = rank(cands, th.max_ranked)
    excluded = [c for c in cands if c.blocked]
    lines = [f"# 割安カタリスト・レポート {asof}", "",
             f"- 候補 {len(ranked)}銘柄(合議スコア順)/足切りを通って型に該当した銘柄 {len(cands)}/"
             f"開示を読んだ銘柄 {len(catalysts)}", ""]
    if not catalysts:
        lines += ["> ⚠ 開示読解(LLM)の結果がまだありません。型の重なりだけで並べています。"
                  "`/value-daily` で開示を読ませてから作り直すと、カタリストの点が加わります。", ""]
    if not ranked:
        lines += ["## 今日の結論", "", "**該当なし。** 型が2つ以上重なる銘柄も、型が1つ+カタリストのある銘柄もありません。"
                  "これは正常な結果です。", ""]
    risks: list[float] = []
    for i, c in enumerate(ranked, start=1):
        s, f = c.snapshot, c.snapshot.fin
        lines += [f"## {i}. {c.code} {c.name}(スコア {c.score:.1f})", "",
                  f"- 型: {' / '.join(f'{k}:{TYPE_NAMES[k]}' for k in c.types_hit)}  ・  業種 {s.sector33}  ・  "
                  f"株価 {s.price:,.0f}円  ・  時価総額 {s.mcap / 1e8:,.0f}億円  ・  売買代金(20日平均) {s.turnover / 1e4:,.0f}万円"]
        for k in c.types_hit:
            for r in c.screens[k].reasons:
                lines.append(f"  - [{k}] {r}")
        if c.catalyst and c.catalyst.strength != "None":
            k = c.catalyst
            lines += [f"- カタリスト: {'・'.join(k.types)}(強さ {k.strength}、時期 {k.timing})", f"  - 要約: {k.summary}",
                      f"  - 強化の読み: {k.bull_case}", f"  - **反対仮説**: {k.bear_case}", f"  - 次に確認: {k.confirm_next}"]
        else:
            lines.append("- カタリスト: " + ("開示に有力なものなし" if c.catalyst else "未読(開示読解の結果なし)"))
        for d in c.dangers:
            lines.append(f"- {'🚫' if d.severity == 'block' else '⚠'} {d.text}")
        stop = risk.stop_price(s.prices, s.price, th)
        if env.capital and stop is not None:
            sz = risk.position_size(env.capital, s.price, stop, s.turnover, th)
            if sz.shares:
                risks.append(sz.risk_yen)
                lines.append(f"- 損失の限定(判断材料。指示ではない): 損切りの目安 {stop:,.0f}円(幅 {sz.stop_distance:.0%})、"
                             f"株数の上限 {sz.shares:,}株(約{sz.amount_yen:,.0f}円、最大損失 {sz.risk_yen:,.0f}円、制約: {sz.limited_by})")
            else:
                lines.append(f"- 損失の限定: {'、'.join(sz.notes)}")
        elif stop is not None:
            lines.append(f"- 損切りの目安 {stop:,.0f}円(config の value.capital_yen を設定すると株数の上限も出す)")
        lines.append("")
    if env.capital and risks:
        for m in risk.portfolio_check(env.capital, risks, th):
            lines.append(f"> ⚠ {m}")
    if excluded:
        lines += ["## 除外された銘柄(型には該当したが、危険信号で除外)", ""]
        for c in excluded:
            lines.append(f"- {c.code} {c.name}: " + "、".join(d.text for d in c.dangers if d.severity == "block"))
    reports = env.app.dir("reports")
    md = reports / f"value_{asof}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    export = env.app.dir("export")
    csv_path = export / f"value_{asof}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:   # 他のモデルとの連携用(CONCEPT §3)
        w = csv.writer(fh)
        w.writerow(["date", "code", "name", "score", "types", "catalyst_strength", "price", "stop"])
        for c in ranked:
            stop = risk.stop_price(c.snapshot.prices, c.snapshot.price, th)
            w.writerow([asof, c.code, c.name, f"{c.score:.2f}", "".join(c.types_hit),
                        c.catalyst.strength if c.catalyst else "未読", f"{c.snapshot.price:.0f}",
                        "" if stop is None else f"{stop:.0f}"])
    return md, csv_path


# ---- 過去検証・評価 --------------------------------------------------------------------

def backtest(env: ValueEnv, start: date, end: date, require_delisted: bool = False) -> Path:
    topix_code = env.app.topix_code()
    topix = env.prices.daily(topix_code, start - timedelta(days=10), end + timedelta(days=90))
    if topix.empty:
        raise ValueError(f"TOPIX(prices.topix_code={topix_code})の株価が取れません")
    dates = bt.month_end_dates(start, end)
    res = bt.run_backtest(env.universe, env.fin, env.prices, topix, dates, env.th, require_delisted)
    out = env.app.dir("reports") / f"value_backtest_{start}_{end}.md"
    out.write_text(bt.render_markdown(res, env.th, start, end), encoding="utf-8")
    if not res.obs.empty:
        res.obs.to_csv(out.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    return out


def run_eval(outputs_path: Path, eval_dir: Path | None = None) -> tuple[list[dict], dict, list[str]]:
    cases = evalset.load_cases(eval_dir or REPO_ROOT / "knowledge" / "value_eval")
    outputs = cat.read_json(outputs_path)
    schema_errors = cat.validate_output(outputs)
    if schema_errors:
        return [], {}, schema_errors
    results, summary = evalset.run_eval(cases, outputs)
    return results, summary, []
