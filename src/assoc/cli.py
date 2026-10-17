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
    failed = 0
    for name, r in summary.items():
        ok = getattr(r, "ok", True)
        failed += not ok
        msg = getattr(r, "message", "") or ""
        print(f"  {'✓' if ok else '✗'} {name}: {getattr(r, 'items', r)} 件 {msg}")
    return 1 if failed else 0


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


def cmd_alert(args) -> int:
    """見出しの急増と代表銘柄の値動きを見て、新しいものを通知する(毎時の収集のあとに自動でも動く)。"""
    from assoc.alerts import run_alerts
    new = run_alerts(_app(args), notify=not args.no_toast, price=False if args.no_price else None)
    for a in new:
        print(f"速報: {a.title}\n  {a.detail}")
    if not new:
        print("新しい速報はありません")
    return 0


def cmd_prepare(args) -> int:
    app = _app(args)
    day = _date(args.date)
    # 代表銘柄の株価を補完して、テーマ別の騰落率を作り直してからパックを作る(D37。従来は手動で、株価が数日古いままだった)
    if day is None or day == app.today():
        from assoc.market import theme_returns
        try:
            info = theme_returns.refresh(app, day or app.today())
            note = f" ※補完失敗: {info['fill_error']}" if "fill_error" in info else ""
            print(f"テーマ別の騰落率: {info['themes']}テーマ(基準日 {info['as_of']}){note}")
        except Exception as e:  # 株価が取れなくても、ニュース側のパックは作る
            print(f"テーマ別の騰落率を更新できませんでした({type(e).__name__}: {e})。前回の値で続けます")
    print(f"入力パック: {app.prepare(day)}")
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


def cmd_prices(args) -> int:
    """株価の重ね合わせの状態確認(status)と、空白期間の補完(fill)。"""
    from assoc.market import layered_prices as lp
    app = _app(args)
    if args.action == "status":
        for row in lp.LayeredPriceSource(lp.layers_from_config(app.cfg)).status(app.today()):
            if not row["exists"]:
                print(f"  ✗ {row['name']}: ファイルがありません({row['path']})")
                continue
            print(f"  {row['name']}: {row['codes']}銘柄 / 最新 {row['max_date']}({row['stale_days']}日前)"
                  f" / 期間 {row['min_date']}〜 / ファイル更新 {row['file_mtime']}")
        return 0
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all:
        rows = app.con.execute(
            "SELECT code FROM universe_daily WHERE date = (SELECT max(date) FROM universe_daily) "
            "AND market LIKE '%内国株式%' ORDER BY code").fetchall()
        codes = [r[0] for r in rows]
    else:
        rows = app.con.execute("SELECT DISTINCT code FROM price_copy").fetchall()
        codes = [r[0] for r in rows]
    codes = list(dict.fromkeys(codes + [app.topix_code()]))
    r = lp.fill_gap(app.cfg, codes, today=app.today(), start=_date(args.start))
    print(f"空白補完: 要求 {r['requested']}銘柄 → 取得できた {r['ok']}銘柄、"
          f"ダウンロード {r['rows_downloaded']}行(開始日 {r['start']})")
    print(f"  ファイル: {r['file_codes']}銘柄・{r['file_rows']}行 {r['path']}")
    if r["empty"]:
        print(f"  取得できなかった銘柄 {len(r['empty'])}件(先頭10件): {', '.join(r['empty'][:10])}")
    return 0


def cmd_watch(args) -> int:
    """候補ウォッチ:本命・監視の銘柄に新しい材料があるかを点検する(トークンは使わない)。"""
    from assoc.watch import build_watch
    app = _app(args)
    w = build_watch(app, app.today(), save_state=not args.dry_run)
    print(f"対象 {w['targets']}銘柄 / 新しい材料あり {w['with_new_material']}銘柄(前回の点検 {w['since']} 以降)")
    for r in w["items"]:
        if r["new_material"]:
            print(f"  {r['code']} {r['company_name']}: 開示 {len(r['disclosures'])}件・ニュース {len(r['news'])}件"
                  + (f"・決算 {r['earnings_date']}" if r["earnings_soon"] else ""))
    return 0


def cmd_export(args) -> int:
    """共有棚(data/shared/)を更新する。他のシステムが連想側の収集結果を読み取り専用で使うための出力。"""
    from assoc.shared_export import export_shared
    app = _app(args)
    r = export_shared(app.con, app.data / "shared", app.today())
    print(f"共有棚: 書き出し {r['written']} / 変更なし {r['skipped']} / ファイル数 {r['files']}({app.data / 'shared'})")
    return 0


def _refresh_shared(args) -> None:
    """収集・入力パック・翌日の処理のあとに共有棚を更新する。失敗しても本来の処理は止めない。"""
    try:
        from assoc.shared_export import export_shared
        app = _app(args)
        export_shared(app.con, app.data / "shared", app.today())
    except Exception as e:
        print(f"(共有棚の更新に失敗: {type(e).__name__}: {str(e)[:120]})")


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


def cmd_decide(args) -> int:
    """本命に対するあなたの判断(買う/監視/見送り)を記録する(CONCEPT §9.1。任意)。"""
    app = _app(args)
    picks = [p for p in app.store.read("pick_tracking") if p.get("event") == "first_pick" and p["code"] == args.code]
    if not picks:
        print(f"エラー: {args.code} は本命に挙げた記録がありません")
        return 1
    p = picks[-1]
    from assoc.timeutil import to_iso, utcnow
    app.store.append("user_decision", {"scenario_id": p["scenario_id"], "code": args.code,
                                       "candidate_id": p.get("candidate_id"), "action": args.action,
                                       "reason": args.reason or "", "decided_at": to_iso(utcnow())})
    print(f"記録しました: {args.code} {args.action}({p['scenario_id']})")
    return 0


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

    s = sub.add_parser("alert", help="日中の速報:見出しの急増・代表銘柄の値動きを見て、新しいものを通知する")
    s.add_argument("--no-toast", action="store_true", help="Windows の通知を出さない")
    s.add_argument("--no-price", action="store_true", help="株価を見ない(見出しだけ)")
    s.set_defaults(func=cmd_alert)

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

    s = sub.add_parser("prices", help="株価の重ね合わせの状態確認(status)・空白期間の補完(fill)")
    s.add_argument("action", choices=["status", "fill"])
    s.add_argument("--codes", help="fill の対象(カンマ区切り)。省略時は、記録に使った銘柄")
    s.add_argument("--all", action="store_true", help="fill の対象を、上場している内国株式すべてにする(時間がかかる)")
    s.add_argument("--start", help="fill の開始日 YYYY-MM-DD(省略時は既存の履歴の最新日の10日前)")
    s.set_defaults(func=cmd_prices)

    s = sub.add_parser("pastcase", help="過去事例ライブラリの値動きを計算する")
    s.add_argument("action", choices=["compute"])
    s.set_defaults(func=cmd_pastcase)

    s = sub.add_parser("decide", help="本命に対するあなたの判断を記録する(任意)")
    s.add_argument("code", help="銘柄コード")
    s.add_argument("action", choices=["買う", "監視", "見送り"])
    s.add_argument("--reason", help="理由(一言)")
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("dashboard", help="ダッシュボード(data/reports/dashboard.html)を作る")
    s.add_argument("--open", action="store_true", help="作ったあとブラウザで開く")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("watch", help="候補ウォッチ:本命・監視の銘柄に新しい材料があるかを点検する")
    s.add_argument("--dry-run", action="store_true", help="前回の点検時刻を更新しない")
    s.set_defaults(func=cmd_watch)

    sub.add_parser("export", help="共有棚(data/shared/)を更新する(他のシステムが読み取り専用で使う)").set_defaults(func=cmd_export)
    sub.add_parser("verify", help="記録が書き換えられていないか確認する").set_defaults(func=cmd_verify)
    sub.add_parser("backup", help="記録を複製する").set_defaults(func=cmd_backup)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")      # Windows のコンソールで文字化けしないように
    from assoc.netfix import ensure_ascii_ca_bundle
    ensure_ascii_ca_bundle()                          # 日本語パスの仮想環境でも yfinance が動くように
    args = build_parser().parse_args(argv)
    try:
        code = args.func(args)
    except (FileNotFoundError, ValueError) as e:
        # 設定の不足や、確定する出力が無いなど、利用者が直せるものは1行で案内する
        print(f"エラー: {e}")
        return 1
    if args.command in ("report", "nextday", "collect", "commit") and code == 0:
        _refresh_dashboard(args)
    if args.command in ("collect", "prepare", "nextday") and code == 0:
        _refresh_shared(args)
    if args.command == "collect" and code == 0:
        # 収集のたびに速報を見る(D38)。失敗しても収集の結果は変えない
        try:
            from assoc.alerts import run_alerts
            for a in run_alerts(_app(args)):
                print(f"速報: {a.title}")
        except Exception as e:
            print(f"速報の確認に失敗: {type(e).__name__}: {e}")
    return code
