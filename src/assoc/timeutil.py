"""時刻と営業日。保存は UTC、表示は日本時間、営業日は東証のカレンダー(docs/DESIGN.md §2)。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import jpholiday

JST = ZoneInfo("Asia/Tokyo")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """UTC の ISO 形式にそろえる。タイムゾーンのない日時は受け付けない。"""
    if dt.tzinfo is None:
        raise ValueError("タイムゾーンのない日時は保存できません")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"タイムゾーンのない日時です: {s}")
    return dt


def jst_date(dt: datetime) -> date:
    return dt.astimezone(JST).date()


def is_business_day(d: date) -> bool:
    """東証の営業日。土日、祝日、年末年始(12/31〜1/3)を休みとする。"""
    if d.weekday() >= 5 or jpholiday.is_holiday(d):
        return False
    if (d.month == 12 and d.day == 31) or (d.month == 1 and d.day <= 3):
        return False
    return True


def add_business_days(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d += timedelta(days=step)
        if is_business_day(d):
            remaining -= 1
    return d


def business_days_between(start: date, end: date) -> int:
    """start の翌営業日から end までの営業日数(start == end なら 0)。"""
    if end < start:
        return -business_days_between(end, start)
    count, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_business_day(d):
            count += 1
    return count
