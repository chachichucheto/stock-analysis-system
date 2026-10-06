"""screen → (LLM) → commit → report を、CLI から通しで動かす。外部への接続はしない。"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assoc.cli import main
from assoc.config import load_config
from assoc.store import db
from assoc.store.records import RecordStore
from value_helpers import ASOF, days_ending

FIN_HEADER = ("code,period_end,disclosed_date,period,cash,receivables,securities,investment_securities,allowance,"
              "current_assets,total_assets,total_liabilities,equity,interest_debt,revenue_ttm,operating_income_ttm,"
              "net_income_ttm,shares_ex_treasury\n")
GOOD_FIN = "{c},2026-03-31,2026-05-15,FY,5e9,2e9,0,1e9,0,8.5e9,11e9,2e9,9e9,0.5e9,10e9,0.5e9,0.4e9,10000000\n"
PLAIN_FIN = "{c},2026-03-31,2026-05-15,FY,1e9,1e9,0,0,0,3e9,8e9,5e9,3e9,0.5e9,10e9,0.5e9,0.4e9,10000000\n"
TITLE = "自己株式の取得に関するお知らせ"


@pytest.fixture
def env(tmp_path):
    prices, value, data = tmp_path / "prices", tmp_path / "value", tmp_path / "data"
    for d in (prices, value, data):
        d.mkdir()
    days = days_ending(ASOF, 150)
    for code, close in (("1000", 350.0), ("2000", 350.0), ("1306", 2000.0)):
        with (prices / f"{code}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            for d in days:
                w.writerow([d, close, close, close, close, 200000])
    (value / "universe.csv").write_text(
        "code,name,market,sector33,listed_date,delisted_date,monitoring,going_concern\n"
        "1000,割安工業,スタンダード,機械,2001-01-01,,0,0\n2000,普通商事,スタンダード,卸売業,2001-01-01,,0,0\n"
        "3000,廃止化学,スタンダード,化学,2001-01-01,2024-01-05,0,0\n", encoding="utf-8")
    (value / "financials.csv").write_text(FIN_HEADER + GOOD_FIN.format(c="1000") + PLAIN_FIN.format(c="2000"), encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"paths:\n  data_dir: {data.as_posix()}\nprices:\n  source: csv_dir\n  csv_dir: {prices.as_posix()}\n"
                   f"  topix_code: '1306'\nvalue:\n  dir: {value.as_posix()}\n  capital_yen: 10000000\n", encoding="utf-8")
    con = db.connect(data)
    con.execute("INSERT INTO disclosure VALUES (?,?,?,?,?,?,?,?,?)",
                ["disc1", "tdnet", "10000", "割安工業", TITLE, "その他", "https://example.invalid/1",
                 datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc), datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)])
    con.close()
    return {"cfg": str(cfg), "value": value, "data": data}


def run(env, *args):
    return main(["--config", env["cfg"], "value", *args, "--date", ASOF.isoformat()] if args[0] != "commit"
                else ["--config", env["cfg"], "value", *args])


def good_output(quote=TITLE):
    return {"asof": ASOF.isoformat(), "items": [{
        "key": "1000", "catalyst_types": ["株主還元"], "strength": "Medium", "timing": "1〜3か月",
        "summary": "自己株式の取得が決まった", "bull_case": "資産に対して割安なうえ、還元が始まる",
        "bear_case": "取得額が小さく、需給への効果は限定的かもしれない",
        "facts": [{"text": "自己株式の取得を開示", "source_id": "disc1", "quote": quote}],
        "confirm_next": "取得枠の消化率"}]}


def test_full_flow(env, capsys):
    assert run(env, "screen") == 0
    pack = json.loads((env["value"] / f"pack_{ASOF}.json").read_text(encoding="utf-8"))
    assert [it["key"] for it in pack["items"]] == ["1000"]                # 型Aに該当するのは1000だけ
    assert pack["items"][0]["disclosures"][0]["title"] == TITLE           # 5桁コード(末尾0)の開示も拾う
    assert pack["items"][0]["types_hit"] == ["A"]

    # LLM 前の報告:カタリストが無いので、型が1つだけの銘柄は候補にならない
    assert run(env, "report") == 0
    md = (env["data"] / "reports" / f"value_{ASOF}.md").read_text(encoding="utf-8")
    assert "該当なし" in md and "開示読解(LLM)の結果がまだありません" in md

    # 出所のない引用は弾く
    inbox = env["value"] / "inbox"
    inbox.mkdir()
    bad = inbox / f"value_{ASOF}.json"
    bad.write_text(json.dumps(good_output(quote="取得総額 50億円"), ensure_ascii=False), encoding="utf-8")
    assert run(env, "commit") == 1
    assert "根拠なし" in capsys.readouterr().out

    bad.write_text(json.dumps(good_output(), ensure_ascii=False), encoding="utf-8")
    assert run(env, "commit") == 0
    assert run(env, "report") == 0
    md = (env["data"] / "reports" / f"value_{ASOF}.md").read_text(encoding="utf-8")
    assert "## 1. 1000 割安工業" in md and "反対仮説" in md and "損切りの目安" in md and "株数の上限" in md
    assert "2000" not in md                                               # 型に該当しない銘柄は載らない
    rows = list(csv.DictReader((env["data"] / "export" / f"value_{ASOF}.csv").open(encoding="utf-8-sig")))
    assert [r["code"] for r in rows] == ["1000"] and rows[0]["catalyst_strength"] == "Medium"

    store = RecordStore(env["data"] / "records")
    assert [r["code"] for r in store.read("value_candidate")] == []       # screen 時点では型が1つだけで候補なし
    assert store.verify("value_catalyst") == [] and len(list(store.read("value_catalyst"))) == 1


def test_commit_requires_matching_pack(env):
    inbox = env["value"] / "inbox"
    inbox.mkdir()
    out = good_output()
    out["asof"] = "2026-09-01"
    (inbox / "value_2026-09-01.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    assert main(["--config", env["cfg"], "value", "commit"]) == 1


def test_missing_inputs_give_a_one_line_error(env, capsys):
    (env["value"] / "financials.csv").unlink()
    assert run(env, "screen") == 1
    assert "財務データがありません" in capsys.readouterr().out


def test_eval_cli_runs_on_shipped_samples(tmp_path, capsys):
    from assoc.value import evalset
    cases = evalset.load_cases(Path(__file__).resolve().parents[1] / "knowledge" / "value_eval")
    items = [{"key": c["id"], "catalyst_types": [], "strength": "None", "timing": "不明", "summary": "", "bull_case": "",
              "bear_case": "", "facts": [], "confirm_next": ""} for c in cases]
    f = tmp_path / "out.json"
    f.write_text(json.dumps({"asof": "2026-10-06", "items": items}, ensure_ascii=False), encoding="utf-8")
    assert main(["value", "eval", "--file", str(f)]) == 0
    assert "fictional" in capsys.readouterr().out


def test_backtest_cli_requires_range(env, capsys):
    assert run(env, "backtest") == 1
    assert "--start" in capsys.readouterr().out


def test_check_cli_reports_and_exit_code(env, capsys):
    assert run(env, "check") == 0                                  # 廃止銘柄あり・開示日も正常 → エラーなし
    out = capsys.readouterr().out
    assert "銘柄マスタ 3銘柄" in out and "[警告]" in out          # 3000 は財務データなし、他にも警告が出る
    (env["value"] / "financials.csv").write_text(
        FIN_HEADER + "1000,2026-03-31,2026-03-01,FY,1,1,0,0,0,1,1,1,1,0,1,1,1,1000\n", encoding="utf-8")
    assert run(env, "check") == 1
    assert "先読み" in capsys.readouterr().out
