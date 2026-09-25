# 過去事例ライブラリ

`docs/CONCEPT.md` §6.2、`docs/DESIGN.md` §6.10。過去の大きなニュースで、関連銘柄がどう反応したかを記録する。
**大衆は過去の反応を参考に売買する**ので、過去の反応は投資家の連想の元ネタそのものである。

## ファイルの形式

1事例1ファイル(`PCnnnn_短い名前.yaml`)。

```yaml
case_id: PC0001
title: 事例の名前
event_date: 2011-03-11          # イベントの日(日本時間)
event_summary: 何が起きたか
theme_ids: [P10]                 # THEMES.md の ID
time_type: 局面展開               # 単発 | 局面展開 | 予定日
phases:                          # 局面展開型のみ
  - name: パニック
    until: 2011-03-15
related:                         # 関連銘柄。動かなかった銘柄も必ず入れる
  - code: "1812"
    company_name: 鹿島建設
    stage: 2                     # 連想の段数
    viewpoint: 投資家             # 経済 | 投資家 | 両方
    why_related: 復興需要
    # ↓ 値動きは Python が計算して埋める(python -m assoc pastcase compute)。手で書かない
    max_excess_20d: null
    moved: null
    days_to_move: null
notes: 気づいたこと
sources:                         # 事例の説明の根拠(URL)
  - https://...
```

## 決まり

- 値動き(`max_excess_20d`、`moved`、`days_to_move`)は、**株価データから Python が計算する**。LLM や人が手で書かない。
- 上がった銘柄だけでなく、**関連がありながら動かなかった銘柄**を必ず入れる(材料の大きさを過大に見積もらないため)。
- 過去事例を LLM に解かせて「当てられた」としても、探す能力の証明には使わない(`CONCEPT.md` §10)。
