"""Relative-date resolution (Contract 7). ONE implementation shared by validator, gold-task
generator and tests, so "last quarter" means the same thing everywhere.

All presets are calendar-based and resolve against `as_of` (the dataset's as_of_date,
never today's date). Ranges are inclusive ISO dates.

Presets:  last_7_days  last_30_days  last_90_days  last_12_months
          this_month  last_month  this_quarter  last_quarter  this_year  last_year  year_to_date
Explicit: month:2018-03   quarter:2018Q2   year:2017   between:2018-01-01:2018-03-31
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _month_range(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _quarter_range(y: int, q: int) -> tuple[date, date]:
    m1 = 3 * (q - 1) + 1
    return date(y, m1, 1), _month_range(y, m1 + 2)[1]


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    i = y * 12 + (m - 1) + delta
    return i // 12, i % 12 + 1


def resolve(preset: str, as_of: str) -> tuple[str, str]:
    a = _d(as_of)
    q = (a.month - 1) // 3 + 1
    if preset == "last_7_days":
        r = (a - timedelta(days=6), a)
    elif preset == "last_30_days":
        r = (a - timedelta(days=29), a)
    elif preset == "last_90_days":
        r = (a - timedelta(days=89), a)
    elif preset == "last_12_months":
        y, m = _shift_month(a.year, a.month, -11)
        r = (date(y, m, 1), a)
    elif preset == "this_month":
        r = (date(a.year, a.month, 1), a)
    elif preset == "last_month":
        r = _month_range(*_shift_month(a.year, a.month, -1))
    elif preset == "this_quarter":
        r = (_quarter_range(a.year, q)[0], a)
    elif preset == "last_quarter":
        y, qq = (a.year, q - 1) if q > 1 else (a.year - 1, 4)
        r = _quarter_range(y, qq)
    elif preset == "this_year":
        r = (date(a.year, 1, 1), date(a.year, 12, 31))
    elif preset == "year_to_date":
        r = (date(a.year, 1, 1), a)
    elif preset == "last_year":
        r = (date(a.year - 1, 1, 1), date(a.year - 1, 12, 31))
    elif m := re.fullmatch(r"month:(\d{4})-(\d{2})", preset):
        r = _month_range(int(m[1]), int(m[2]))
    elif m := re.fullmatch(r"quarter:(\d{4})Q([1-4])", preset):
        r = _quarter_range(int(m[1]), int(m[2]))
    elif m := re.fullmatch(r"year:(\d{4})", preset):
        r = (date(int(m[1]), 1, 1), date(int(m[1]), 12, 31))
    elif m := re.fullmatch(r"between:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})", preset):
        r = (_d(m[1]), _d(m[2]))
    else:
        raise ValueError(f"unknown date preset: {preset}")
    if r[0] > r[1]:
        raise ValueError(f"empty range for {preset}: {r}")
    return r[0].isoformat(), r[1].isoformat()


def previous_period(frm: str, to: str) -> tuple[str, str]:
    """The comparison period: previous calendar month/quarter/year if the range is exactly one,
    otherwise the same number of days immediately before."""
    f, t = _d(frm), _d(to)
    if f.day == 1 and t == _month_range(f.year, f.month)[1]:
        return tuple(x.isoformat() for x in _month_range(*_shift_month(f.year, f.month, -1)))  # type: ignore
    if f.month in (1, 4, 7, 10) and f.day == 1 and t == _quarter_range(f.year, (f.month - 1) // 3 + 1)[1]:
        q = (f.month - 1) // 3 + 1
        y, qq = (f.year, q - 1) if q > 1 else (f.year - 1, 4)
        return tuple(x.isoformat() for x in _quarter_range(y, qq))  # type: ignore
    if f == date(f.year, 1, 1) and t == date(f.year, 12, 31):
        return date(f.year - 1, 1, 1).isoformat(), date(f.year - 1, 12, 31).isoformat()
    n = (t - f).days + 1
    return (f - timedelta(days=n)).isoformat(), (f - timedelta(days=1)).isoformat()
