---
description: 割安カタリスト・モデルの手順(開示読解)を実行し、レポートを作る
argument-hint: "[日付 YYYY-MM-DD]"
---

割安カタリスト・モデルの手順を実行します。引数: $ARGUMENTS(日付が無ければ今日)

- Python は必ずリポジトリの仮想環境のもの(`./.venv/Scripts/python.exe`)を使う。
- 日付を指定するときは、各コマンドに `--date YYYY-MM-DD` を付ける。

1. 機械のスクリーンを実行し、入力パックを作る:`./.venv/Scripts/python.exe -m assoc value screen`
   - すでに今日のパック `data/value/pack_YYYY-MM-DD.json` があれば、作り直さない(同じ日の結果を変えないため)。
2. パックの `mode` が「該当なし」なら、手順3〜4を飛ばして手順5へ進む(`"items": []` の出力を作って確定してもよい)。
3. `prompts/value-disclosure-v1.md` を読み、その手順どおりに開示を読んで、`data/value/inbox/value_YYYY-MM-DD.json` を作る。
   - **引用は開示の文面から一字一句そのまま抜く。** 要約や言い換えをすると、確定処理が差し戻す。
4. 確定する:`./.venv/Scripts/python.exe -m assoc value commit`
   - エラーが出たら、表示された理由に従って JSON を直し、もう一度実行する(最大2回)。
5. レポートを作る:`./.venv/Scripts/python.exe -m assoc value report`
6. 最後に、レポートの「候補」(コード・社名・型・カタリスト・反対仮説・損切りの目安)を短く伝える。候補が無ければ「該当なし」と伝える。**売買の指示は書かない。**
