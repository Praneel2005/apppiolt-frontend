"""Gateway to the governed metric routes, plus period arithmetic (default / next / aligned periods).

The analysis engine never builds SQL: it calls /api/metrics/query | compare | trend through their Python functions, so every
number comes from the semantic layer and every call can become evidence.
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import date, timedelta
from typing import Any

from agent.perceive import describe_range, month_range, quarter_range, shift_month
from agent.schema import PeriodSel
from backend import executor
from backend.common import ApiError
from backend.deeplinks import supporting_view
from backend.config import app_model
from backend.routes.metrics import MetricCompare, MetricQuery, TrendQuery, metrics_compare, metrics_query, metrics_trend
from contracts.dates import previous_period, resolve


class AnalysisError(Exception):
    pass


def _d(s: str) -> date:
    return date.fromisoformat(s)


def make_period(start: date | str, end: date | str, preset: str | None = None) -> PeriodSel:
    s = start if isinstance(start, str) else start.isoformat()
    e = end if isinstance(end, str) else end.isoformat()
    return PeriodSel(s, e, preset or f"between:{s}:{e}", describe_range(s, e))


def shift_year(d: date, n: int = -1) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + n, day=28)


def _is_month(s: date, e: date) -> bool:
    return s.day == 1 and e == month_range(s.year, s.month)[1]


def _is_quarter(s: date, e: date) -> bool:
    return s.day == 1 and s.month in (1, 4, 7, 10) and e == quarter_range(s.year, (s.month - 1) // 3 + 1)[1]


def _is_year(s: date, e: date) -> bool:
    return s == date(s.year, 1, 1) and e == date(s.year, 12, 31)


def previous_of(p: PeriodSel) -> PeriodSel:
    f, t = previous_period(p.start, p.end)
    return make_period(f, t)


def next_period(b: PeriodSel, as_of: str) -> PeriodSel | None:
    """The period of the same kind that follows `b`, clipped to the data's as-of date."""
    s, e, a = _d(b.start), _d(b.end), _d(as_of)
    if _is_month(s, e):
        y, m = shift_month(s.year, s.month, 1)
        ns, ne = month_range(y, m)
    elif _is_quarter(s, e):
        q = (s.month - 1) // 3 + 1
        y, qq = (s.year, q + 1) if q < 4 else (s.year + 1, 1)
        ns, ne = quarter_range(y, qq)
    elif _is_year(s, e):
        ns, ne = date(s.year + 1, 1, 1), date(s.year + 1, 12, 31)
    else:
        n = (e - s).days + 1
        ns, ne = e + timedelta(days=1), e + timedelta(days=n)
    if ns > a:
        return None
    return make_period(ns, min(ne, a))


def default_periods(primary: PeriodSel | None, baseline: PeriodSel | None, as_of: str) -> tuple[PeriodSel, PeriodSel, list[str]]:
    notes: list[str] = []
    if primary and baseline:
        return primary, baseline, notes
    if baseline and not primary:
        nxt = next_period(baseline, as_of)
        if nxt is None:
            n = (_d(baseline.end) - _d(baseline.start)).days + 1
            a = _d(as_of)
            nxt = make_period(a - timedelta(days=n - 1), a)
        notes.append(f"Compared {nxt.label} with {baseline.label}.")
        return nxt, baseline, notes
    if primary and not baseline:
        b = previous_of(primary)
        notes.append(f"No comparison period given; compared with the preceding period ({b.label}).")
        return primary, b, notes
    f, t = resolve("this_month", as_of)
    p = make_period(f, t, "this_month")
    b = previous_of(p)
    notes.append(f"No period given; compared the latest month ({p.label}) with the one before ({b.label}).")
    return p, b, notes


_PARTIAL_PRESETS = {"this_month", "this_quarter", "this_year", "year_to_date"}


def align_periods(primary: PeriodSel, baseline: PeriodSel, as_of: str) -> tuple[PeriodSel, PeriodSel, list[str]]:
    """Make the comparison like-for-like: clip to the data, trim the baseline when the current period is incomplete."""
    notes: list[str] = []
    a = _d(as_of)
    ps, pe, bs, be = _d(primary.start), _d(primary.end), _d(baseline.start), _d(baseline.end)
    partial = False
    if pe > a:
        pe, partial = a, True
        notes.append(f"{primary.label} extends past the latest data ({as_of}); using data up to {as_of}.")
    elif primary.preset in _PARTIAL_PRESETS:
        natural_end = (month_range(ps.year, ps.month)[1] if primary.preset == "this_month"
                       else quarter_range(ps.year, (ps.month - 1) // 3 + 1)[1] if primary.preset == "this_quarter"
                       else date(ps.year, 12, 31))
        partial = a < natural_end
    if partial:
        elapsed = (pe - ps).days + 1
        new_be = min(be, bs + timedelta(days=elapsed - 1))
        if new_be != be:
            notes.append(f"The current period is incomplete, so the comparison is like-for-like: the first {elapsed} days of each period.")
        be = new_be
        return make_period(ps, pe), make_period(bs, be), notes
    lp, lb = (pe - ps).days + 1, (be - bs).days + 1
    if lp != lb:
        notes.append(f"The periods have different lengths ({lp} vs {lb} days); totals are not per-day averages.")
    return make_period(ps, pe, primary.preset if pe == _d(primary.end) else None), make_period(bs, be, baseline.preset), notes


def usable_filters(lex, metric: str, filters: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    ok = set(lex.filterable_dims(metric))
    use = {k: v for k, v in filters.items() if k in ok and v}
    dropped = [k for k in filters if k not in ok]
    return use, dropped


class Gateway:
    def __init__(self, ctx):
        self.ctx = ctx
        self.s = ctx.session
        self.lex = ctx.lex
        self._sem = asyncio.Semaphore(4)
        self.evidence_ids: list[str] = []

    async def _run(self, fn, body):
        async with self._sem:
            try:
                return await asyncio.to_thread(fn, body)
            except ApiError as e:
                raise AnalysisError(e.message) from e
            except Exception as e:  # noqa: BLE001  (database timeouts etc. must degrade one step, not the whole turn)
                raise AnalysisError(f"{type(e).__name__}: {str(e)[:160]}") from e

    async def query(self, metric: str, group_by: list[str] | None, filters: dict[str, list[str]] | None,
                    period: PeriodSel | None, sort: str | None = None, limit: int = 100) -> dict:
        body = MetricQuery(metric=metric, group_by=list(group_by or []), filters=dict(filters or {}),
                           date_from=_d(period.start) if period else None, date_to=_d(period.end) if period else None,
                           sort=sort, limit=limit)
        return await self._run(metrics_query, body)

    async def compare(self, metric: str, group_by: str | None, filters: dict[str, list[str]] | None,
                      primary: PeriodSel, baseline: PeriodSel, limit: int = 200) -> dict:
        body = MetricCompare(metric=metric, group_by=group_by, filters=dict(filters or {}),
                             date_from=_d(primary.start), date_to=_d(primary.end),
                             compare_from=_d(baseline.start), compare_to=_d(baseline.end), limit=limit)
        return await self._run(metrics_compare, body)

    async def trend(self, metric: str, filters: dict[str, list[str]] | None, period: PeriodSel | None) -> dict:
        body = TrendQuery(metric=metric, filters=dict(filters or {}),
                          date_from=_d(period.start) if period else None, date_to=_d(period.end) if period else None)
        return await self._run(metrics_trend, body)

    async def evidence(self, kind: str, source: str, values: dict[str, Any]) -> str:
        ev = await executor._evidence(self.s, kind, source, values)
        self.evidence_ids.append(ev.evidence_id)
        return ev.evidence_id

    def view_for(self, metric: str, group_by: list[str] | None, filters: dict[str, list[str]] | None,
                 period: PeriodSel | None) -> dict | None:
        try:
            return supporting_view(app_model(), metric, group_by or [], filters or {},
                                   period.start if period else None, period.end if period else None)
        except Exception:  # noqa: BLE001
            return None
