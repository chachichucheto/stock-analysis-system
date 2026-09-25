---
description: 夜の手順(ニュース連想)を実行し、連想レポートを作る
argument-hint: "[最小] ← 時間がない日は「最小」を付ける"
---

ニュース連想モデルの夜の手順を実行します。引数: $ARGUMENTS

1. 開始を記録する:`python -m assoc session start`
2. 入力パックを用意する:`python -m assoc pack`(今日の `data/packs/pack_YYYY-MM-DD.json` が既にあれば作り直さない)。
   - 引数に「最小」があれば、パックの `mode` を「最小」として扱う。
3. `prompts/daily-v1.md` を読み、その手順どおりに作業する。出力は `data/inbox/daily_YYYY-MM-DD.json`。
4. 確定する:`python -m assoc commit`
   - エラーが出たら、表示された理由に従って JSON を直し、もう一度 `python -m assoc commit` を実行する(最大2回)。
   - 警告(照合できなかった候補、「未確認」への修正など)は、そのまま受け入れてよい。
5. レポートを作る:`python -m assoc report`
6. 終了を記録する:`python -m assoc session end --mode 通常`(最小手順なら `--mode 最小`)
7. 最後に、レポートの「今日の結論」と本命の銘柄(コード・社名・自信度・崩れる条件)を短く伝える。本命が無ければ「該当なし」と伝える。
