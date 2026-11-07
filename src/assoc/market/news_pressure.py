"""悪いニュースの量(D53)。株価が動く前に、ニュース側から相場の傷みに気づくための機械の目印。

株価だけで姿勢を決めると、崩れたあとにしか分からない(手遅れ)。ここでは**その日に出た見出しのうち、悪化を示す語を含むものの数**を、
普段(直前14日の中央値)と比べる。毎日ある悪いニュースでは鳴らず、**普段より急に増えたとき**だけ鳴る。
限界:語の数は「質」を見ない。11/5 の『データセンター規制派が圧勝』のように、悪化語を使わずに前提を変えるニュースは数字に出ない。
その読みは夜の判断(見出しの中身を読む)と、朝の点検・時間ごとの速報の役目。これは気づくための目印で、判断ではない。
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from assoc.config import Thresholds

# 悪化を示す語(暫定)。相場全体・政策・地政学・信用不安の語。個社の不祥事は狙い撃ち(sniper)が拾う
RISK_WORDS = re.compile(
    r"急落|暴落|株安|大幅安|大幅続落|大幅反落|ショック|売り殺到|売りが加速|規制強化|規制案|規制派|制裁|接収|没収|国有化|禁輸|"
    r"破綻|倒産|債務不履行|デフォルト|格下げ|資産凍結|バブル崩壊|資金繰り|信用不安|取り付け|ブラックマンデー|リーマン|"
    r"関税(?:引き上げ|発動|強化)|輸出規制")


@dataclass
class NewsPressure:
    day: date
    count: int                       # その日の、悪化語を含む見出し(重複をまとめた数)
    baseline: float                  # 直前14日の中央値
    ratio: float
    level: str                       # "" / 慎重 / 投資を控える(ニュース側からの目印)
    reasons: list[str] = field(default_factory=list)
    headlines: list[dict[str, Any]] = field(default_factory=list)   # 悪化語が多い順の代表の見出し(title, url, source)

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "count": self.count, "baseline": round(self.baseline, 1),
                "ratio": round(self.ratio, 2), "level": self.level, "reasons": self.reasons, "headlines": self.headlines}


def _key(title: str) -> str:
    return re.sub(r"[\s　|｜\-–—:：【】\[\]（）()「」『』]", "", title or "")[:22]


def news_pressure(con, day: date, th: Thresholds, *, top: int = 8) -> NewsPressure:
    """day(JST の日付)の悪化語を含む見出しの数と、普段との比。"""
    since = day - timedelta(days=th.news_pressure_base_days)
    rows = con.execute(
        "SELECT cast(timezone('Asia/Tokyo', first_observed_at) AS DATE) d, title, url, source FROM news_item "
        "WHERE cast(timezone('Asia/Tokyo', first_observed_at) AS DATE) BETWEEN ? AND ?", [since, day]).fetchall()
    per_day: dict[date, dict[str, tuple]] = defaultdict(dict)
    for d, title, url, source in rows:
        if title and RISK_WORDS.search(title):
            per_day[d].setdefault(_key(title), (title, url, source))
    today = per_day.get(day, {})
    past = [len(per_day.get(since + timedelta(days=i), {})) for i in range((day - since).days)]
    past = [n for n in past if n is not None]
    baseline = max(statistics.median(past) if len(past) >= th.news_pressure_min_days else th.news_pressure_floor,
                   th.news_pressure_floor)
    count = len(today)
    ratio = count / baseline if baseline else 0.0
    level, reasons = "", []
    if count >= th.news_pressure_out_count and ratio >= th.news_pressure_out_ratio:
        level = "投資を控える"
    elif count >= th.news_pressure_warn_count and ratio >= th.news_pressure_warn_ratio:
        level = "慎重"
    if level:
        reasons.append(f"悪化を示す語を含む見出しが {count} 件(普段 {baseline:.0f} 件の {ratio:.1f} 倍)")
    ranked = sorted(today.values(), key=lambda t: -len(RISK_WORDS.findall(t[0])))[:top]
    return NewsPressure(day, count, baseline, ratio, level, reasons,
                        [{"title": t, "url": u, "source": s} for t, u, s in ranked])
