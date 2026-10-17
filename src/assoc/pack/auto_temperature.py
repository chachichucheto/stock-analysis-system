"""テーマの温度の機械更新(docs/DECISIONS.md D37)。

従来、温度は週1回 LLM が書く値だけだった。サイバーは 10/1 に「上昇中」と書いたあと、翌週まで見直されず、
その間の急騰・報道の急増に追随できなかった。毎晩のパック作成で、株価(代表銘柄の騰落率)と見出し件数の
急増から温度の目安を機械で出し、LLM の最新の温度と食い違えば `source: "auto"` の記録を追記する。
LLM が次に書いた温度が、そのあとの最新になる(週1回の更新の日取りは、LLM の記録だけで数える)。

判定(上のものほど優先)
  上昇中:見出し件数が急増している、または 代表銘柄の5日の超過(中央値 − TOPIX)が +5% 以上、または 1か月の超過が +10% 以上
  冷却中:1か月の超過が −8% 以下で、5日の超過も 0 以下(見出しの急増が無いとき)
  それ以外:横ばい(ただし、直前が自動で書いた「上昇中」「冷却中」のときだけ書く。細かい揺れで記録を増やさない)
"""
from __future__ import annotations


def auto_signal(price_state: dict | None, burst: dict | None, th) -> dict:
    """{"hint": 上昇中/横ばい/冷却中, "reasons": [...]}。データが無ければ hint は None。"""
    reasons: list[str] = []
    up = down = False
    if burst:
        up = True
        reasons.append(f"見出し件数が急増(当日{burst['today_count']}件、直近{len(burst['daily'])}日のうち{burst['elevated_days']}日)")
    ex5 = ex1 = None
    if price_state:
        m1, t1 = price_state.get("median_1m"), price_state.get("topix_1m")
        m5, t5 = price_state.get("median_5d"), price_state.get("topix_5d")
        if m1 is not None and t1 is not None:
            ex1 = m1 - t1
        if m5 is not None and t5 is not None:
            ex5 = m5 - t5
        if ex5 is not None and ex5 >= th.temp_up_excess_5d:
            up = True
            reasons.append(f"代表銘柄の5日の超過 {ex5:+.1%}")
        if ex1 is not None and ex1 >= th.temp_up_excess_1m:
            up = True
            reasons.append(f"代表銘柄の1か月の超過 {ex1:+.1%}")
        if not up and ex1 is not None and ex1 <= th.temp_down_excess_1m and (ex5 is None or ex5 <= 0):
            down = True
            reasons.append(f"代表銘柄の1か月の超過 {ex1:+.1%}、5日も下向き")
    if up:
        return {"hint": "上昇中", "reasons": reasons}
    if down:
        return {"hint": "冷却中", "reasons": reasons}
    if burst is None and ex1 is None and ex5 is None:
        return {"hint": None, "reasons": []}
    return {"hint": "横ばい", "reasons": []}


def should_write(hint: str | None, latest: dict | None) -> bool:
    """機械の判定を記録に追記するか。"""
    if hint is None:
        return False
    current = (latest or {}).get("temperature")
    if hint == current:
        return False
    if hint == "横ばい":
        # 「横ばい」は、直前が自動で書いた「上昇中」「冷却中」を戻すときだけ。LLM の温度や未設定のところへは書かない
        return (latest or {}).get("source") == "auto"
    return True
