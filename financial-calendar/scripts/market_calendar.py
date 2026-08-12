"""Deterministic NYSE trading-day rules for the mechanical calendar.

Regular full-day holidays are calculated with the standard library. Exceptional
closures must be listed in ``config/calendar.yaml: market_closures``.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

from common import load_yaml


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    day = dt.date(year, month, 1)
    return day + dt.timedelta(days=(weekday - day.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    nxt = dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    day = nxt - dt.timedelta(days=1)
    return day - dt.timedelta(days=(day.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> dt.date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(year, month, day)


def _observed_fixed(day: dt.date, *, friday_for_saturday: bool = True) -> dt.date:
    if day.weekday() == 5 and friday_for_saturday:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def regular_holidays(year: int) -> frozenset[dt.date]:
    holidays = {
        _observed_fixed(dt.date(year, 1, 1), friday_for_saturday=False),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - dt.timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed(dt.date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed(dt.date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed_fixed(dt.date(year, 6, 19)))
    return frozenset(holidays)


def configured_closures() -> set[dt.date]:
    cal = load_yaml("calendar.yaml")
    return {
        dt.date.fromisoformat(str(row["date"]))
        for row in (cal.get("market_closures") or [])
        if row.get("date")
    }


def is_trading_day(day: dt.date) -> bool:
    return (
        day.weekday() < 5
        and day not in regular_holidays(day.year)
        and day not in configured_closures()
    )


def previous_trading_day(day: dt.date, *, inclusive: bool = True) -> dt.date:
    current = day if inclusive else day - dt.timedelta(days=1)
    while not is_trading_day(current):
        current -= dt.timedelta(days=1)
    return current


def last_trading_day(year: int, month: int) -> dt.date:
    nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return previous_trading_day(nxt - dt.timedelta(days=1))
