# ニュース連想モデル(News Association Model)

強いニュースが出たその夜に、市場がこれから買いに行く銘柄を、根拠付きで上位に挙げる道具。
テスタ氏が得意とする「ニュースからの連想ゲーム」を、個人でも毎日回せる形にしたもの。
ファンダメンタルズ分析・需給分析に続く、3つ目のモデル。

> **4つ目のモデル(割安カタリスト)** も同じリポジトリにある。資産に対して割安な小型株で、再評価のきっかけ(カタリスト)がありそうなものを探す。
> 有名個人投資家の手法の調査をもとにした設計で、**実データの検証が始まった(型Dは不合格、型Aは方向が正だが未確定)。現時点では「勝てる」とは言えない**(→ [docs/VALUE_VALIDATION.md](docs/VALUE_VALIDATION.md)、設計は [docs/VALUE_DESIGN.md](docs/VALUE_DESIGN.md)、ローカルへの指示は [docs/VALUE_LOCAL_KICKOFF.md](docs/VALUE_LOCAL_KICKOFF.md))。

| 文書 | 内容 |
|---|---|
| [docs/CONCEPT.md](docs/CONCEPT.md) | 何を・なぜ作るか(最上位) |
| [docs/DESIGN.md](docs/DESIGN.md) | どう作るか |
| [docs/HANDOFF.md](docs/HANDOFF.md) | Windows での導入・毎日の使い方・バックアップ |
| [docs/LOCAL_KICKOFF.md](docs/LOCAL_KICKOFF.md) | ローカルの Claude Code への作業指示書 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 決定の記録 |
| [docs/THEMES.md](docs/THEMES.md) | テーマの一覧 |
| [docs/ANALYSIS_PLAN.md](docs/ANALYSIS_PLAN.md) | 答え合わせの定義と基準 |
| [docs/INVESTOR_RESEARCH.md](docs/INVESTOR_RESEARCH.md) | 有名個人投資家の手法の調査(割安カタリストの元) |
| [docs/VALUE_VALIDATION.md](docs/VALUE_VALIDATION.md) | 同・「勝てるのか」の検証の記録と、事前登録の合格基準(最初に読む) |
| [docs/VALUE_DESIGN.md](docs/VALUE_DESIGN.md) | 割安カタリスト・モデルの設計 |
| [docs/VALUE_LOCAL_KICKOFF.md](docs/VALUE_LOCAL_KICKOFF.md) | 同・ローカルの Claude Code への作業指示書 |
| [docs/VALUE_CATALYST_PLAN.md](docs/VALUE_CATALYST_PLAN.md) | 同・最初のたたき台(経緯) |

## 毎日の使い方(概要)

- 自動:ニュース・TDnet の収集(1時間ごと)、入力パックの作成(18:15)、等級の確定と状態の更新(翌16:00)
- 手動:夜に Claude Code で `/association-daily`(30分以内)
- 見る:`data\reports\dashboard.html`(ダッシュボード)と `data\reports\report_YYYY-MM-DD.md`(連想レポート)
- 月1回:`/association-monthly`(振り返り)

最初の導入は [docs/HANDOFF.md](docs/HANDOFF.md) の §0 から。
