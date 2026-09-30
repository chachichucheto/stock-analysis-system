# ローカル作業の指示書(ローカルの Claude Code 向け)

あなたは、ユーザーの Windows PC で動く Claude Code です。クラウドで作った「ニュース連想モデル」を、ローカルで動く状態にするのが仕事です。
最初に次の3つを読んでください(読むだけで、まだ何も変更しない)。

1. `README.md`
2. `docs/CONCEPT.md`(何を・なぜ作るか。上位の文書)
3. `docs/HANDOFF.md`(導入手順と既知の制約)

必要になったら `docs/DESIGN.md`(どう作るか)と `docs/DECISIONS.md`(決定の経緯)も読みます。

---

## 0. 前提と守ること

- このリポジトリはクラウド側で作りました。**クラウドからは外部のデータ元(TDnet、NHK、Google News、GDELT、Wikipedia、JPX、EDINET、Yahoo Finance)に一度も接続できていません。** 収集器は見本のデータでしか試しておらず、実際の形式との食い違いが必ずあります。これを直すのが、あなたの最大の仕事です。
- **ユーザーの既存システム(ファンダメンタルズ分析、需給分析、株価DB)は読み取り専用です。** コード・データの変更、上書き、削除は一切しない。このリポジトリは、既存システムとは**別のフォルダ**に置く(既存システムのフォルダの中には置かない)。
- `data\` と `config\config.yaml` は git に入れない(`.gitignore` 済み)。**API キー、個人情報、株価データ、収集したニュース本文をコミットしない。**
- 相手先に負荷をかけない。`collect.request_interval_sec` の間隔を守り、個人利用の範囲で使う。
- 変更は小さく分けてコミットし、`news-association-model` ブランチに push する(クラウド側がそれを取り込む)。コミットメッセージは日本語でよい。
- **分からないこと、既存システムに影響しそうなことは、推測で進めずにユーザーに聞く。**
- 件数や結果は、推測で書かず、実際に実行して確かめたものだけを報告する。

---

## 1. 作業の順序(この順に進める)

### フェーズA【最優先】セットアップと収集の開始(目安30分)

TDnet(適時開示)は約1か月分しか残らず、日が過ぎるほど取れなくなります。最初にこれを終わらせます。

1. `git clone -b news-association-model https://github.com/chachichucheto/stock-analysis-system.git` を、既存システムとは別の場所で実行する。
2. `powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1`(仮想環境・ライブラリ・設定ファイルの作成)。
3. `.\.venv\Scripts\python.exe -m pytest -q`。**167件すべて通ることを確認する。** 通らなければ原因を調べて報告する(Windows 固有の問題の可能性がある)。
4. `.\.venv\Scripts\python.exe -m assoc collect` を実行し、収集器ごとの成否と件数を確認する。
5. `.\.venv\Scripts\python.exe -m assoc backfill-tdnet` で、TDnet の過去約1か月分を取り込む。
6. 収集の自動実行だけを登録する:`powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -CollectOnly`。

### フェーズB 収集器を本物のデータに合わせる

フェーズAの結果で、件数が0の収集器・エラーの出た収集器・件数が不自然に少ない収集器を、**1つずつ**直します。優先順位は TDnet → RSS → Google News → EDINET → JPX 上場銘柄一覧 → GDELT → Wikipedia。

各収集器のファイルの冒頭に、想定している形式と「ローカルで最初に確認すべき点」が書いてあります。特に次を確かめてください。

| 収集器 | 確認すること |
|---|---|
| `ingest/tdnet.py`(最重要) | 実際の HTML の構造(クラス名、ページ送りの終わり方、文字コード)。ずれていれば解析部分を直す |
| `ingest/rss.py` | NHK などの RSS が取れるか。`config.yaml` の RSS の URL が生きているか(死んでいるものは外す) |
| `ingest/google_news.py` | 媒体名の取り出し、件数。取得制限に当たらないか |
| `ingest/edinet.py` | API キーの設定(ユーザーに取得を依頼。無ければスキップでよい)、書類の種類の判定 |
| `master/universe.py` | JPX の上場銘柄一覧の実際の URL・形式・列名(`collect.jpx_universe_url` を直す)。**監理・整理ポストの一覧の取得元**も探して実装する |
| `master/calendar.py` | 決算発表予定日の配布元と形式(`collect.earnings_schedule_url`)。見つからなければ、その旨を報告する |
| `ingest/gdelt.py` / `ingest/wikipedia.py` | 日本語のキーワード・記事名で実際に値が取れるか(`config.yaml` の `wikipedia_articles` の記事名が存在するか) |

直し方の決まり:
- 実際の応答から**小さな見本**(個人情報・本文を含まない、数件分)を作り、`tests\fixtures\` に足して、解析のテストを更新する。**テストは常に全件通る状態を保つ。**
- 解析部分(純関数)と取得部分を分けている構造は変えない。
- 直したら、そのファイルの冒頭の「想定している形式」も実際に合わせて書き換える。
- `python -m assoc collect` を再実行して、件数と成否を確かめる。`python -m assoc doctor` で接続の確認もする。

### フェーズC 既存資産の確認(S1)と株価DBの接続

`docs/DESIGN.md` §11 の「S1 の確認項目」1〜9を、**読み取りだけ**で調べ、結果を `docs/S1_REPORT.md` にまとめます(個人情報・キーは書かない)。

1. 株価DB:場所、形式(CSV / SQLite / その他)、対象銘柄、更新される時刻、欠損、株式分割の調整、**上場廃止した銘柄が残っているか**、TOPIX または 1306(TOPIX 連動 ETF)が入っているか、日足の列(始値・高値・安値・終値・出来高)
2. 既存システムの銘柄マスタ(業種、時価総額)、営業日カレンダー
3. 既存の需給システムの信用残・出来高のデータ
4. 既存システムに、ニュース・TDnet・EDINET に関するコードやデータがあるか
5. 再利用できる共通処理(銘柄コードの正規化など)
6. 既存の大化け株研究のデータ(過去事例ライブラリの素材として使えるか)
7. 触ってはいけない箇所の一覧
8. 株探(kabutan.jp)・kabukarin.net の利用規約と robots.txt(自動取得してよいか)
9. 内閣官房「日本成長戦略」の戦略17分野の正式な名称と、62品目(`docs/THEMES.md` の §1 と照合する。食い違いがあれば `THEMES.md` の修正案を `S1_REPORT.md` に書く)

そのうえで、**株価DBを `src/assoc/market/prices.py` から読めるようにします。**

- 既存の形式に合わせて `PriceSource` を実装した新しいクラスを足し、`config\config.yaml` の `prices` で切り替えられるようにする(`price_source_from_config` に分岐を足す)。**既存のDBは読み取り専用で開く。**
- `.\.venv\Scripts\python.exe -m assoc doctor` で「株価DB ✓」になることを確認する。
- **株価DBの更新時刻**を調べて報告する。夜の等級確定(`nextday`、タスクは16:00)は、その日の終値がDBに入ってから走る必要がある。DBの更新が16:00より遅いなら、`scripts\windows\register_tasks.ps1` の `assoc_nextday` の時刻をずらす提案を書く。
- TOPIX(または 1306)が無ければ、その代わりの指数・ETFを `prices.topix_code` に設定する。

### フェーズD 過去事例の計算と、残りの自動実行の登録

1. `.\.venv\Scripts\python.exe -m assoc pastcase compute`(過去事例30件の値動きを株価DBから計算し、銘柄コードと社名を照合する)。
   - 「⚠ 照合」と出た銘柄は、コードか社名が間違っている。`code_confidence: check` の銘柄を優先して調べ、正しいコード・社名に直す(上場廃止で銘柄マスタに無い銘柄は、その旨をメモする)。
   - 株価が取れず計算できない銘柄(上場廃止など)の一覧も報告する。
2. `powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1`(残りのタスクも登録)。
3. **バックアップ**:OneDrive の中に `assoc_backup` フォルダを作り、`config\config.yaml` の `paths.backup_dir` に設定する(`docs/HANDOFF.md` の「バックアップ」の手順。ユーザーに OneDrive のサインイン状況を確認する)。`python -m assoc backup` で複製できることを確認する。

### フェーズE 予行演習(最初の夜の手順)

収集と株価DBの接続が終わったら、**夜の手順を1回、予行演習として実行します**(`/association-daily`)。これは、指示文(`prompts/daily-v1.md`)が実際の LLM にとって実行可能かを確かめる最初の機会です。

1. `/association-daily` を実行する。
2. つまずいた点(指示文のわかりにくい箇所、入力パックに足りない情報、スキーマが厳しすぎる箇所、確定処理のエラーなど)を記録する。
3. 出力された連想レポート(`data\reports\report_YYYY-MM-DD.md`)とダッシュボード(`data\reports\dashboard.html`)を、ユーザーに見せる。
4. **予行演習の結果は、正式な記録の開始前なので、必要なら `data\` を消して作り直してよい**(ユーザーに確認してから)。正式な記録は、ユーザーが「今日から本番」と言った夜から始める。

---

## 2. 報告(クラウド側に渡す)

各フェーズの終わりに、次の形で短く報告してください。ユーザーが、その内容をクラウド側のセッションに貼ります。

```
フェーズ:A〜E のどれか
実施したこと:
実行結果(件数は実際に確かめたものだけ):
  - 収集器ごとの成否と件数
  - テストの件数と結果
直したファイル:
見つかった問題(直せたもの/直せていないもの):
ユーザーへの質問・確認したいこと:
クラウド側で直してほしいこと(設計や指示文の問題など):
次にやること:
```

全フェーズが終わったら、`docs/S1_REPORT.md` を含めて push し、ユーザーに「push した」と伝えてください。

---

## 3. うまくいかないとき

- **`setup.ps1` が実行できない**:PowerShell の実行ポリシーの問題の可能性。`-ExecutionPolicy Bypass` を付けているか確認する。それでも止まるなら、止まった場所のメッセージをそのまま報告する。
- **`pytest` が一部通らない**:Windows のパス・文字コード・改行の違いが原因のことが多い。原因を調べて、テスト側でなく本体のコードの問題なら直す。直せなければ報告する。
- **ある収集器がどうしても取れない**:その収集器だけ飛ばして先に進み(`collect --sources` で他を動かす)、取れない理由を報告する。無理に回避策を作り込まない。
- **既存システムの構造がよく分からない**:推測で触らず、ユーザーに聞く。
