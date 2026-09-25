---
description: 月次の振り返り(見逃し・外れの分析と改善の提案)を行う
argument-hint: "[YYYY-MM] ← 省略時は先月"
---

月次の振り返りを行います。対象月: $ARGUMENTS(省略時は先月)

1. 集計する:`python -m assoc monthly prepare --month <対象月>`
2. `prompts/monthly-v1.md` を読み、その手順どおりに作業する。出力は `data/inbox/monthly_<対象月>.json`。
3. 確定してレポートを作る:`python -m assoc monthly commit --month <対象月>`
4. 最後に、振り返りレポートの要点(3つの指標、見逃しの主な原因、改善の提案)を短く伝え、どの改善を反映するかをユーザーに確認する。**ユーザーの了承なしに指示文や閾値を変えない。**
