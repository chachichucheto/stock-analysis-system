"""コマンドライン:python -m assoc <サブコマンド>(docs/HANDOFF.md §1・§2)。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from assoc.config import load_config


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _app(args):
    from assoc.app import App
    return App(load_config(args.config))


def cmd_doctor(args) -> int:
    from assoc.doctor import run_doctor
    return run_doctor(load_config(args.config), online=not args.offline)


def cmd_collect(args) -> int:
    summary = _app(args).collect(args.sources.split(",") if args.sources else None)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_backfill_tdnet(args) -> int:
    """TDnet に残っている過去分(約1か月)をまとめて取り込む。初回に1度だけ実行する。"""
    from datetime import timedelta
    from assoc.ingest import tdnet
    from assoc.ingest.common import safe_run
    app = _app(args)
    end = app.today()
    start = end - timedelta(days=args.days)
    r = safe_run(app.con, "tdnet", lambda: tdnet.fetch_tdnet(app.cfg, app.con, start, end))
    print(f"TDnet {start}〜{end}: {r}")
    return 0


def cmd_prepare(args) -> int:
    print(f"入力パック: {_app(args).prepare(_date(args.date))}")
    return 0


def cmd_commit(args) -> int:
    r = _app(args).commit(Path(args.file) if args.file else None)
    if not r.ok:
        print("確定できませんでした。次を直して、もう一度 python -m assoc commit を実行してください:")
        for e in r.errors:
            print(f"  - {e}")
        return 1
    print(f"確定しました: {r.counts}")
    if r.scenario_ids:
        print(f"シナリオ番号: {r.scenario_ids}")
    for w in r.warnings:
        print(f"  警告: {w}")
    return 0


def cmd_report(args) -> int:
    md, csv_path = _app(args).report(_date(args.date))
    print(f"連想レポート: {md}\n連携ファイル: {csv_path}")
    return 0


def cmd_nextday(args) -> int:
    print(json.dumps(_app(args).nextday(_date(args.date)), ensure_ascii=False))
    return 0


def cmd_morning(args) -> int:
    notices = _app(args).morning(_date(args.date))
    print("\n".join(notices) or "大きな反応なし")
    return 0


def cmd_session(args) -> int:
    from assoc import session
    app = _app(args)
    if args.action == "start":
        print(f"開始: {session.start(app.data)}")
    elif args.action == "end":
        row = session.end(app.data, app.store, args.mode, args.model or "")
        print(f"終了: 所要 {row['duration_min']} 分")
    else:
        if not args.reason:
            print("--reason で理由を指定してください")
            return 1
        session.skip(app.store, args.reason)
        print("実行しなかった日として記録しました")
    return 0


def cmd_monthly(args) -> int:
    from assoc.review import monthly
    app = _app(args)
    month = args.month or monthly.previous_month(app.today())
    if args.action == "prepare":
        print(f"集計: {monthly.prepare(app, month)}")
    else:
        r = monthly.commit(app, month)
        if not r["ok"]:
            print("確定できませんでした:\n" + "\n".join(f"  - {e}" for e in r["errors"]))
            return 1
        print(f"振り返りレポート: {r['report']}")
    return 0


def cmd_pastcase(args) -> int:
    from assoc.review.past_cases import compute_all
    for line in compute_all(_app(args)):
        print(line)
    return 0


def cmd_dashboard(args) -> int:
    from assoc.dashboard.build import build_dashboard
    path = build_dashboard(_app(args))
    print(f"ダッシュボード: {path}")
    if args.open:
        import webbrowser
        webbrowser.open(path.resolve().as_uri())
    return 0


def _refresh_dashboard(args) -> None:
    """レポート・翌日の処理・収集のあとにダッシュボードを作り直す。失敗しても本来の処理は止めない。"""
    try:
        from assoc.dashboard.build import build_dashboard
        build_dashboard(_app(args))
    except Exception as e:
        print(f"(ダッシュボードの更新に失敗: {e})")


def cmd_verify(args) -> int:
    problems = _app(args).verify()
    print("\n".join(problems) or "記録は正常です(書き換えは見つかりません)")
    return 1 if problems else 0


def cmd_backup(args) -> int:
    dest = _app(args).backup()
    print(f"複製先: {dest}" if dest else "paths.backup_dir が未設定のため、複製しませんでした")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="assoc", description="ニュース連想モデル")
    p.add_argument("--config", help="設定ファイル(既定: config/config.yaml)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("doctor", help="設定・フォルダ・接続を確認する")
    s.add_argument("--offline", action="store_true", help="データ元への接続確認をしない")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("collect", help="ニュース・TDnet などを収集する")
    s.add_argument("--sources", help="カンマ区切り(例: tdnet,rss)。省略時はすべて")
    s.set_defaults(func=cmd_collect)

    s = sub.add_parser("backfill-tdnet", help="TDnet の過去分(約1か月)をまとめて取り込む")
    s.add_argument("--days", type=int, default=31)
    s.set_defaults(func=cmd_backfill_tdnet)

    for name, func, help_ in (("prepare", cmd_prepare, "イベント化・足切り・入力パックの作成"),
                              ("pack", cmd_prepare, "prepare と同じ(既にあれば作り直さない)"),
                              ("report", cmd_report, "ランキングを計算し、連想レポートを作る"),
                              ("nextday", cmd_nextday, "等級の確定・状態の更新・売りのサイン・「動いた」の判定"),
                              ("morning", cmd_morning, "米国の1段目の反応を確認する")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--date", help="対象日 YYYY-MM-DD(省略時は今日)")
        s.set_defaults(func=func)

    s = sub.add_parser("commit", help="夜の手順の出力を検証して確定する")
    s.add_argument("--file", help="確定するファイル(省略時は data/inbox の最新)")
    s.set_defaults(func=cmd_commit)

    s = sub.add_parser("session", help="夜の手順の実行記録")
    s.add_argument("action", choices=["start", "end", "skip"])
    s.add_argument("--mode", default="通常", choices=["通常", "最小"])
    s.add_argument("--model", help="使ったモデル名")
    s.add_argument("--reason", help="skip の理由")
    s.set_defaults(func=cmd_session)

    s = sub.add_parser("monthly", help="月次の振り返り")
    s.add_argument("action", choices=["prepare", "commit"])
    s.add_argument("--month", help="YYYY-MM(省略時は先月)")
    s.set_defaults(func=cmd_monthly)

    s = sub.add_parser("pastcase", help="過去事例ライブラリの値動きを計算する")
    s.add_argument("action", choices=["compute"])
    s.set_defaults(func=cmd_pastcase)

    s = sub.add_parser("dashboard", help="ダッシュボード(data/reports/dashboard.html)を作る")
    s.add_argument("--open", action="store_true", help="作ったあとブラウザで開く")
    s.set_defaults(func=cmd_dashboard)

    sub.add_parser("verify", help="記録が書き換えられていないか確認する").set_defaults(func=cmd_verify)
    sub.add_parser("backup", help="記録を複製する").set_defaults(func=cmd_backup)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")      # Windows のコンソールで文字化けしないように
    args = build_parser().parse_args(argv)
    code = args.func(args)
    if args.command in ("report", "nextday", "collect", "commit") and code == 0:
        _refresh_dashboard(args)
    return code
