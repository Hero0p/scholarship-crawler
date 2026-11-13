"""Adaptive recrawl policy (PROJECT_PLAN.md §14)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from atlas import config


def in_season(today: date | None = None) -> bool:
    today = today or date.today()
    return today.month in config.frequency()["season_months"]


def base_hours(source_type: str) -> int:
    table = config.frequency()["base_hours"]
    pair = table.get(source_type, table["UNKNOWN"])
    return pair[0] if in_season() else pair[1]


def next_crawl(kind: str, *, source_type: str, unchanged_streak: int = 0, failures: int = 0,
               closing: date | None = None, opening: date | None = None, changed: bool = False,
               now: datetime | None = None) -> datetime:
    f = config.frequency()
    now = now or datetime.utcnow()
    if failures:
        retry = f["failure_retry"]
        return now + timedelta(hours=retry[min(failures - 1, len(retry) - 1)])
    if kind == "IRRELEVANT" or kind == "SOFT_404":
        return now + timedelta(hours=f["irrelevant_page"])
    if kind != "SCHOLARSHIP":
        return now + timedelta(hours=base_hours(source_type))
    d = f["detail_page"]
    today = now.date()
    o = f["deadline_overrides"]
    if closing:
        days = (closing - today).days
        if 0 <= days <= 3:
            return now + timedelta(hours=o["within_3_days"])
        if 0 <= days <= 15:
            return now + timedelta(hours=o["within_15_days"])
        if days < 0:
            since = -days
            for w in o["extension_watch_days"]:
                if since < w:
                    return datetime.combine(closing + timedelta(days=w), datetime.min.time()) + timedelta(hours=20, minutes=30)
            return now + timedelta(hours=o["after_deadline"])
    if opening and 0 <= (opening - today).days <= 7:
        return now + timedelta(hours=o["opening_within_7_days"])
    if changed:
        return now + timedelta(hours=min(d["base"], 24))
    hours = min(d["max"], max(d["min"], d["base"] * (d["backoff"] ** unchanged_streak)))
    return now + timedelta(hours=hours)
