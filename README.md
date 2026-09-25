# ニュース連想モデル(News Association Model)

強いニュースが出たその夜に、市場がこれから買いに行く銘柄を、根拠付きで上位に挙げる道具。
テスタ氏が得意とする「ニュースからの連想ゲーム」を、個人でも毎日回せる形にしたもの。
ファンダメンタルズ分析・需給分析に続く、3つ目のモデル。

| 文書 | 内容 |
|---|---|
| [docs/CONCEPT.md](docs/CONCEPT.md) | 何を・なぜ作るか(最上位) |
| [docs/DESIGN.md](docs/DESIGN.md) | どう作るか |
| [docs/HANDOFF.md](docs/HANDOFF.md) | Windows での導入・毎日の使い方・バックアップ |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 決定の記録 |
| [docs/THEMES.md](docs/THEMES.md) | テーマの一覧 |
| [docs/ANALYSIS_PLAN.md](docs/ANALYSIS_PLAN.md) | 答え合わせの定義と基準 |

## 毎日の使い方(概要)

- 自動:ニュース・TDnet の収集(1時間ごと)、入力パックの作成(18:15)、等級の確定と状態の更新(翌16:00)
- 手動:夜に Claude Code で `/association-daily`(30分以内)
- 見る:`data\reports\dashboard.html`(ダッシュボード)と `data\reports\report_YYYY-MM-DD.md`(連想レポート)
- 月1回:`/association-monthly`(振り返り)

最初の導入は [docs/HANDOFF.md](docs/HANDOFF.md) の §0 から。
