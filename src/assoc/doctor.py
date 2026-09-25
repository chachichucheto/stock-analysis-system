"""動作確認(python -m assoc doctor)。設定・フォルダ・株価DB・データ元への接続・記録を確認する。"""
from __future__ import annotations

from datetime import timedelta

from assoc.config import Config

SOURCES = {
    "NHK RSS": "https://www.nhk.or.jp/rss/news/cat0.xml",
    "Google News": "https://news.google.com/rss/search?q=%E5%8D%8A%E5%B0%8E%E4%BD%93&hl=ja&gl=JP&ceid=JP:ja",
    "TDnet": "https://www.release.tdnet.info/inbs/I_main_00.html",
    "GDELT": "https://api.gdeltproject.org/api/v2/doc/doc?query=semiconductor&mode=timelinevol&format=json",
    "Wikipedia": "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/ja.wikipedia/all-access/2026/01/01",
    "JPX": "https://www.jpx.co.jp/",
    "EDINET": "https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2026-01-05&type=1",
    "Yahoo Finance": "https://query1.finance.yahoo.com/v8/finance/chart/1306.T",
}


def run_doctor(cfg: Config, online: bool = True) -> int:
    from assoc.app import App

    ok = True
    print("■ 設定")
    print(f"  データの置き場所: {cfg.data_dir}")
    print(f"  株価の読み方: {cfg.section('prices').get('source')}")
    app = App(cfg)

    print("■ 株価DB")
    try:
        from assoc.market.prices import price_source_from_config
        src = price_source_from_config(cfg)
        code = str(cfg.section("prices").get("topix_code", "1306"))
        df = src.daily(code, app.today() - timedelta(days=30), app.today())
        if df.empty:
            ok = False
            print(f"  ✗ {code} の株価が読めません(prices の設定を確認してください)")
        else:
            print(f"  ✓ {code} の株価を {len(df)} 日分読めました(最新 {df['date'].iloc[-1]})")
    except Exception as e:
        ok = False
        print(f"  ✗ {e}")

    print("■ 銘柄マスタ")
    n = app.con.execute("SELECT count(*) FROM universe_daily WHERE date = (SELECT max(date) FROM universe_daily)").fetchone()[0]
    print(f"  {'✓' if n else '△'} 上場銘柄 {n} 件" + ("" if n else "(python -m assoc collect で取得してください)"))

    if online:
        import requests
        ua = cfg.section("collect").get("user_agent", "assoc-research/0.1")
        print("■ データ元への接続")
        for name, url in SOURCES.items():
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": ua})
                mark = "✓" if r.status_code < 400 else "✗"
                if r.status_code >= 400:
                    ok = False
                print(f"  {mark} {name}: HTTP {r.status_code}")
            except Exception as e:
                ok = False
                print(f"  ✗ {name}: {type(e).__name__}")

    print("■ 記録")
    problems = app.verify()
    print("  ✓ 書き換えは見つかりません" if not problems else "\n".join(f"  ✗ {p}" for p in problems))
    ok = ok and not problems
    print("\n結果:", "問題なし" if ok else "要確認の項目があります")
    return 0 if ok else 1
