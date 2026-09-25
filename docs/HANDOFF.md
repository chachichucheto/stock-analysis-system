# ローカル(Windows)への移管手順

クラウドで作ったものを、あなたの Windows PC で動かすための手順書です。更新のたびに §5 の「更新の記録」に追記します。

---

## 1. 初回だけ行うこと

### 1.1 必要なもの

- Python 3.11 以上(`python --version` で確認。無ければ python.org から入れる。インストール時に「Add python.exe to PATH」にチェック)
- Git(`git --version` で確認)
- Claude Code

### 1.2 取り込み

PowerShell を開き、置きたいフォルダで実行します。

```powershell
git clone -b claude/winner-chronology-handoff-b0ou3r https://github.com/chachichucheto/stock-analysis-system.git
cd stock-analysis-system
```

### 1.3 セットアップ

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
```

次のことを自動で行います。
- 仮想環境(`.venv`)の作成とライブラリのインストール
- `config\config.yaml` の作成(見本からコピー)
- 動作確認(`assoc doctor`)

### 1.4 設定

`config\config.yaml` をメモ帳などで開き、次を設定します。

| 項目 | 設定する内容 |
|---|---|
| `prices.source` / `prices.csv_dir` | 既存の株価データの場所と形式。**S1 の確認のあとで、ローカルの Claude Code に合わせてもらう** |
| `paths.backup_dir` | 記録の複製先(OneDrive のフォルダなど)。空なら複製しない |
| `edinet.api_key` | EDINET API のキー(無料登録。無くても他は動く) |

### 1.5 動作確認

```powershell
.\.venv\Scripts\python.exe -m assoc doctor
.\.venv\Scripts\python.exe -m pytest -q
```

`doctor` は、設定・フォルダ・各データ元への接続を確認して、結果を一覧で表示します。

### 1.6 毎日の自動実行の登録

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1
```

| タスク | 時刻 | 内容 |
|---|---|---|
| assoc_collect | 1時間ごと | ニュース・TDnet などの収集 |
| assoc_prepare | 18:00 | イベント化・足切り・入力パックの作成 |
| assoc_morning | 7:30 | 米国の1段目の反応の確認 |
| assoc_nextday | 16:00 | 等級の確定・状態の更新・売りのサイン |
| assoc_backup | 23:30 | 記録の複製 |

解除するときは `register_tasks.ps1 -Remove` を実行します。ログは `data\logs\` に出ます。
PC の電源が切れていた時間の分は、次に起動したときに実行されます(「開始時刻を過ぎた場合はすぐに実行」の設定)。

---

## 2. 毎日の使い方

1. 夜(20時以降)に、このフォルダで Claude Code を起動する
2. `/association-daily` と入力する(時間がない日は `/association-daily 最小`)
3. 最後に表示される本命と、`data\reports\` の連想レポートを見る

実行しない日は、次のコマンドで理由を記録しておくと、振り返りで役に立ちます。

```powershell
.\.venv\Scripts\python.exe -m assoc session skip --reason "出張のため"
```

月に1回、Claude Code で `/association-monthly` を実行します。

---

## 3. 更新の受け取り方

```powershell
cd stock-analysis-system
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

`data\` と `config\config.yaml` は git の管理外なので、`git pull` で消えたり上書きされたりしません。

---

## 4. S1(ローカルで最初にやること)

ローカルの Claude Code に、次のように頼んでください。

> docs/DESIGN.md §11 の S1 を実施して。確認結果を docs/S1_REPORT.md にまとめ、株価DBの読み取り部分(src/assoc/market/prices.py)と config/config.yaml を既存のデータに合わせて。そのあと各収集器を実際に動かして、形式の違いがあれば直して。

各収集器のファイルの冒頭に「ローカルで最初に確認すべき点」が書いてあります。

---

## 5. 更新の記録

| 日付 | 内容 | ローカルで必要な作業 |
|---|---|---|
| 2026-09-25 | 設計書一式(CONCEPT / DESIGN / DECISIONS / THEMES / ANALYSIS_PLAN) | なし |
| 2026-09-25 | 実装の土台、確定処理、指示文とコマンド、Windows 用スクリプト(作業中) | §1 の初回セットアップ |
