# 開示読解の評価セット

`prompts/value-disclosure-v1.md` の出力を、正解と比べて精度を測るためのセット(docs/VALUE_DESIGN.md §8)。

## 形式(1事例=1ファイル)

```yaml
id: VE0001                    # 重複しない番号
fictional: false              # true は形式の見本。本番の精度の集計に含めない
company: 社名(任意)
code: "1234"                  # 任意
disclosed_at: 2025-05-15      # 任意
input:
  disclosures:
    - id: d1                  # 出力の facts[].source_id がこれを指す
      title: 開示のタイトル
      body_excerpt: |         # 本文の要点部分(原文そのまま。引用の照合に使うので要約しない)
        ...
expected:
  catalyst_types: [株主還元]   # 正解。無ければ [](= カタリストなし)
  strength: Medium            # None | Weak | Medium | Strong
  timing: 1〜3か月            # 任意
  note: 正解の根拠(事後の株価の動きなど。任意)
```

## 作り方の決まり(ローカルで本物を作るとき)

1. **本物の開示**(TDnet・EDINET の原文)から作る。**作り話の開示を足して精度を測らない。**
2. 「正解」は、**開示の後の実際の値動き**と照らして決める(ありそうな解釈で決めない)。
   - 開示後60営業日以内に TOPIX 超過 +10% 以上 → Medium 以上の候補
   - 動かなかった開示 → None / Weak(**動かなかった事例を、動いた事例と同数集める**)
3. 成功例だけを集めない。外れ・動かなかった例を半数以上にする。
4. 最初は30件。件数が少ないうちは、精度は参考値(`summarize` が `small_sample` を立てる)。
5. 開示の原文には著作権・利用条件がある。リポジトリは非公開のままにし、第三者に公開しない。

`fictional: true` の事例(VE_SAMPLE*)は、評価器の自己テストと形式の見本にだけ使う。
