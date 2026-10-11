"""The analysis engine: what a careful analyst does when asked "why did X change", "compare A and B", "how are we doing".

All arithmetic is here (and in the metric routes); no LLM is involved. Each function is an async generator that yields
progress steps ("step", {...}) and finally ("result", <dataclass>). Every numeric block is recorded as evidence.

Analyst checks implemented:
  * period alignment (default periods, clipping to the data, like-for-like when the current period is incomplete)
  * headline change and a premise check ("sales dropped" when they rose)
  * identity decomposition (revenue = orders x average order value) with exact volume / rate effects
  * driver scan over every available dimension, ranked by a localisation score, with offsets shown
  * drill-down into the biggest driver
  * confounder metrics (related quality / volume metrics that moved materially)
  * seasonality (what happened over the same transition a year earlier) and year over year
  * trend context (streak, outlier against recent months) and data-window caveats
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, AsyncIterator

from agent.perceive import month_range
from agent.dag import Task, run_plan
from agent.reasoning.rigor import build_rigor
from agent.reasoning.gateway import (AnalysisError, Gateway, align_periods, default_periods, make_period, shift_year,
                                     usable_filters)
from agent.schema import Interp, PeriodSel


def _d(s: str) -> date:
    return date.fromisoformat(s)


# ------------------------------------------------------------------------------------ result types
@dataclass
class DimScan:
    dim: str
    mode: str                       # additive | mix_rate | indicative
    n_groups: int
    gross: float                    # sum of absolute group changes
    top3_share: float
    localisation: float             # excess concentration over a uniform spread (0 = broad-based)
    movers: list[dict]              # groups moving with the overall change, biggest first
    offsets: list[dict]             # groups moving against it
    tail: float                     # part of the change not covered by the listed groups
    rows: list[dict]
    evidence_id: str | None = None


@dataclass
class DrillResult:
    dim: str
    group: str
    scan: DimScan


@dataclass
class ExplainResult:
    metric: str
    title: str
    unit: str
    additive: bool
    higher_is_better: bool
    primary: PeriodSel
    baseline: PeriodSel
    filters: dict[str, list[str]]
    current: float
    previous: float
    delta: float
    pct: float | None
    direction: str
    expected_direction: str
    premise: str
    head_evidence: str
    notes: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    identity: dict | None = None
    scans: list[DimScan] = field(default_factory=list)
    drill: DrillResult | None = None
    confounders: list[dict] = field(default_factory=list)
    seasonality: dict | None = None
    trend: dict | None = None
    evidence_ids: list[str] = field(default_factory=list)
    depth: str = "full"
    rigor: Any = None               # agent.reasoning.rigor.Rigor: unusual?, counterfactual, interaction, checks


@dataclass
class SliceCompareResult:
    metric: str
    title: str
    unit: str
    additive: bool
    period: PeriodSel | None
    slices: list[dict]              # label, filters, value, share_of_total, factors, related, top_groups
    gap_abs: float | None
    gap_pct: float | None
    ratio: float | None
    leader: str | None
    identity: dict | None
    differences: list[dict]         # related metrics that differ materially
    mix_dim: str | None
    notes: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class HealthResult:
    primary: PeriodSel
    baseline: PeriodSel
    items: list[dict]
    notes: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------ helpers
def candidate_dims(lex, metric: str, filters: dict[str, list[str]]) -> list[str]:
    dims = []
    for d in lex.dims_of_metric(metric):
        info = lex.dims.get(d)
        if d in lex.skip_driver_dims or not info or not info.values:
            continue
        if d in filters and len(filters[d]) == 1:
            continue
        if any(lex.dim_parents.get(k) == d for k in filters):
            continue
        dims.append(d)
    return dims


def _rows_from_compare(res: dict, additive: bool) -> tuple[str, list[dict]]:
    rd = res.get("ratio_decomposition")
    if additive:
        return "additive", [{"group": r["group"], "current": r.get("current"), "previous": r.get("previous"),
                             "delta": float(r.get("delta") or 0.0), "pct_change": r.get("pct_change")}
                            for r in res.get("drivers", [])]
    if rd:
        return "mix_rate", [{"group": g["group"], "current": g.get("value_current"), "previous": g.get("value_previous"),
                             "delta": float(g.get("total_effect") or 0.0), "rate_effect": g.get("rate_effect"),
                             "mix_effect": g.get("mix_effect"), "pct_change": None} for g in rd.get("groups", [])]
    return "indicative", [{"group": r["group"], "current": r.get("current"), "previous": r.get("previous"),
                           "delta": float(r.get("delta") or 0.0), "pct_change": r.get("pct_change")}
                          for r in res.get("drivers", [])]


async def scan_dimension(gw: Gateway, metric: str, additive: bool, dim: str, filters: dict, primary: PeriodSel,
                         baseline: PeriodSel) -> DimScan | None:
    try:
        res = await gw.compare(metric, dim, filters, primary, baseline, limit=200)
    except AnalysisError:
        return None
    total = res.get("delta")
    mode, rows = _rows_from_compare(res, additive)
    if total is None or not rows:
        return None
    sgn = 1 if total >= 0 else -1
    for r in rows:
        r["contribution_pct"] = round(100 * r["delta"] / total, 1) if total else None
    ordered = sorted(rows, key=lambda r: -abs(r["delta"]))
    gross = sum(abs(r["delta"]) for r in rows)
    n = sum(1 for r in rows if abs(r["delta"]) > 0)
    top3 = sum(abs(r["delta"]) for r in ordered[:3])
    top3_share = top3 / gross if gross else 0.0
    loc = max(0.0, top3_share - 3.0 / n) if (gross > 0 and n > 3) else 0.0
    movers = [r for r in ordered if r["delta"] * sgn > 0]
    offsets = [r for r in ordered if r["delta"] * sgn < 0]
    tail = float(total) - sum(r["delta"] for r in rows)
    ev = await gw.evidence("contribution", metric, {**res, "rows": rows, "dimension": dim, "mode": mode,
                                                    "view": gw.view_for(metric, [dim], filters, primary)})
    return DimScan(dim, mode, len(rows), gross, top3_share, loc, movers, offsets, tail, rows, ev)


async def _identity(gw: Gateway, lex, metric: str, filters: dict, primary: PeriodSel, baseline: PeriodSel) -> dict | None:
    ident = next((i for i in lex.identities if i["metric"] == metric), None)
    if not ident or len(ident.get("factors", [])) < 2:
        return None
    f1, f2 = ident["factors"][:2]
    if f1 not in lex.metrics or f2 not in lex.metrics:
        return None
    try:
        u1, _ = usable_filters(lex, f1, filters)
        u2, _ = usable_filters(lex, f2, filters)
        r1, r2 = await asyncio.gather(gw.compare(f1, None, u1, primary, baseline), gw.compare(f2, None, u2, primary, baseline))
    except AnalysisError:
        return None
    n0, n1 = r1["previous"]["value"], r1["current"]["value"]
    a0, a1 = r2["previous"]["value"], r2["current"]["value"]
    if None in (n0, n1, a0, a1):
        return None
    vol = (n1 - n0) * a0
    rate = n1 * (a1 - a0)
    total = n1 * a1 - n0 * a0
    out = {"explain": ident.get("explain", ""), "volume": {"metric": f1, "title": lex.metrics[f1].title, "previous": n0,
                                                          "current": n1, "pct": r1.get("pct_change"), "effect": vol},
           "rate": {"metric": f2, "title": lex.metrics[f2].title, "previous": a0, "current": a1, "pct": r2.get("pct_change"),
                    "effect": rate},
           "total": total, "unit": lex.metrics[metric].unit}
    out["evidence_id"] = await gw.evidence("contribution", metric, {**out, "rows": [
        {"factor": out["volume"]["title"], "previous": n0, "current": n1, "effect": vol},
        {"factor": out["rate"]["title"], "previous": a0, "current": a1, "effect": rate}]})
    return out


async def _confounders(gw: Gateway, lex, metric: str, filters: dict, primary: PeriodSel, baseline: PeriodSel,
                       skip: set[str]) -> list[dict]:
    ids = [m for m in lex.related.get(metric, []) if m in lex.metrics and m != metric and m not in skip]

    async def one(mid: str) -> dict | None:
        mi = lex.metrics[mid]
        use, dropped = usable_filters(lex, mid, filters)
        try:
            r = await gw.compare(mid, None, use, primary, baseline)
        except AnalysisError:
            return None
        cur, prev, delta = r["current"]["value"], r["previous"]["value"], r.get("delta")
        if cur is None or prev is None or delta is None:
            return None
        pct = r.get("pct_change")
        adverse = (delta < 0 and mi.higher_is_better) or (delta > 0 and not mi.higher_is_better)
        material = pct is not None and abs(pct) >= lex.materiality_pct
        return {"metric": mid, "title": mi.title, "unit": mi.unit, "current": cur, "previous": prev, "delta": delta,
                "pct": pct, "adverse": bool(adverse) and delta != 0, "material": material,
                "filters_dropped": dropped}

    res = [r for r in await asyncio.gather(*(one(m) for m in ids)) if r]
    if res:
        eid = await gw.evidence("comparison", metric, {"rows": res, "note": "related metrics over the same two periods"})
        for r in res:
            r["evidence_id"] = eid
    return res


async def _seasonality(gw: Gateway, metric: str, filters: dict, primary: PeriodSel, baseline: PeriodSel) -> dict | None:
    def sh(p: PeriodSel) -> PeriodSel:
        return make_period(shift_year(_d(p.start)), shift_year(_d(p.end)))

    pl, bl = sh(primary), sh(baseline)
    try:
        yoy, ly = await asyncio.gather(gw.compare(metric, None, filters, primary, pl),
                                       gw.compare(metric, None, filters, pl, bl))
    except AnalysisError:
        return None
    out: dict[str, Any] = {"last_year_period": pl.label, "last_year_baseline": bl.label}
    ok = False
    if yoy["previous"]["value"]:
        out.update(yoy_pct=yoy.get("pct_change"), yoy_previous=yoy["previous"]["value"])
        ok = True
    if ly["previous"]["value"] and ly["current"]["value"] is not None:
        out.update(ly_transition_pct=ly.get("pct_change"), ly_current=ly["current"]["value"], ly_previous=ly["previous"]["value"])
        ok = True
    if not ok:
        return None
    out["evidence_id"] = await gw.evidence("comparison", metric, {**out, "rows": [
        {"what": "year over year", "pct_change": out.get("yoy_pct")},
        {"what": f"{bl.label} -> {pl.label}", "pct_change": out.get("ly_transition_pct")}]})
    return out


def trend_summary(tr: dict) -> dict:
    rows = tr.get("rows", [])
    vals = [r["value"] for r in rows if r.get("value") is not None]
    n = len(vals)
    streak, sdir = 0, 0
    for i in range(n - 1, 0, -1):
        diff = vals[i] - vals[i - 1]
        s = 1 if diff > 0 else -1 if diff < 0 else 0
        if i == n - 1:
            sdir, streak = s, (1 if s else 0)
        elif s == sdir and s != 0:
            streak += 1
        else:
            break
    z = None
    if n >= 5:
        hist = vals[max(0, n - 7):n - 1]
        mean = sum(hist) / len(hist)
        sd = (sum((x - mean) ** 2 for x in hist) / len(hist)) ** 0.5
        if sd > 0:
            z = (vals[-1] - mean) / sd
    st = tr.get("stats", {})
    return {"months": n, "trend": st.get("trend"), "streak": streak, "streak_dir": sdir, "z_latest": z,
            "peak": st.get("peak"), "trough": st.get("trough"), "first_month": (st.get("first") or {}).get("month"),
            "missing_months": st.get("missing_months", []), "largest_fall": st.get("largest_fall"),
            "largest_rise": st.get("largest_rise")}


def _trend_window(primary: PeriodSel) -> PeriodSel:
    end = _d(primary.end)
    last_day = month_range(end.year, end.month)[1]
    if end != last_day:
        end = month_range(end.year, end.month)[0] - timedelta(days=1)
    y, m = end.year, end.month - 11
    while m < 1:
        y, m = y - 1, m + 12
    return make_period(date(y, m, 1), end)


async def _trend_context(gw: Gateway, metric: str, filters: dict, primary: PeriodSel, record: bool = True) -> dict | None:
    try:
        tr = await gw.trend(metric, filters, _trend_window(primary))
    except AnalysisError:
        return None
    out = trend_summary(tr)
    tk = next((k for k in ((tr.get("rows") or [{}])[0]) if k not in ("value", "mom_pct", "yoy_pct")), "month")
    out["series"] = [r.get("value") for r in tr.get("rows", [])]              # the monthly values, for "is this change unusual?"
    out["series_labels"] = [str(r.get(tk)) for r in tr.get("rows", [])]
    if record:
        out["evidence_id"] = await gw.evidence("trend", metric, {**tr, "view": gw.view_for(metric, [tk], filters, None)})
    return out


# ------------------------------------------------------------------------------------ explain / compare periods
async def explain(ctx, it: Interp, gw: Gateway, depth: str = "full") -> AsyncIterator[tuple[str, Any]]:
    """depth: headline | compare (headline + identity + best splits) | full (adds drill, confounders, seasonality, trend)."""
    lex = ctx.lex
    mi = lex.metrics[it.metric]
    filters, dropped = usable_filters(lex, it.metric, it.filters)
    caveats: list[str] = []
    if dropped:
        caveats.append(f"{mi.title} cannot be filtered by {', '.join(dropped)}; those filters were not applied.")
    primary, baseline, notes = default_periods(it.primary, it.baseline, ctx.as_of)
    primary, baseline, n2 = align_periods(primary, baseline, ctx.as_of)
    notes += n2
    yield "step", {"title": "Aligning the two periods", "detail": f"{primary.label} vs {baseline.label}"}

    head = await gw.compare(it.metric, None, filters, primary, baseline)
    cur, prev, delta, pct = head["current"]["value"], head["previous"]["value"], head.get("delta"), head.get("pct_change")
    if cur is None or prev is None or delta is None:
        raise AnalysisError(f"There is no {mi.title.lower()} data in {primary.label} or {baseline.label}.")
    if prev == 0:
        caveats.append(f"{baseline.label} had a value of zero, so a percentage change is not defined.")
    head_ev = await gw.evidence("comparison", it.metric, {**head, "rows": [
        {"period": baseline.label, "value": prev}, {"period": primary.label, "value": cur}],
        "view": gw.view_for(it.metric, [], filters, primary)})
    direction = "flat" if (pct is not None and abs(pct) < 0.5) or delta == 0 else ("up" if delta > 0 else "down")
    exp = it.expected_direction
    premise = "none" if exp == "none" else "flat" if direction == "flat" else "ok" if exp == direction else "contradicted"
    yield "step", {"title": "Headline change", "detail": f"{mi.title} {direction}" + (f" {pct:+.1f}%" if pct is not None else "")}

    res = ExplainResult(metric=it.metric, title=mi.title, unit=mi.unit, additive=mi.additive,
                        higher_is_better=mi.higher_is_better, primary=primary, baseline=baseline, filters=filters,
                        current=cur, previous=prev, delta=delta, pct=pct, direction=direction, expected_direction=exp,
                        premise=premise, head_evidence=head_ev, notes=notes, caveats=caveats, depth=depth)
    if depth == "headline" or direction == "flat" and depth != "full":
        res.evidence_ids = list(gw.evidence_ids)
        yield "result", res
        return

    dims = candidate_dims(lex, it.metric, filters)
    if it.group_by and depth == "compare":
        dims = [d for d in it.group_by if d in dims] or dims

    # The analysis as a task plan: everything that only needs the two periods starts at once; a task waits only for what it uses.
    def scan_task(d: str) -> Task:
        async def run(_inputs):
            return await scan_dimension(gw, it.metric, mi.additive, d, filters, primary, baseline)
        return Task(f"scan:{d}", f"By {d.replace('_', ' ')}", run, kind="scan")

    async def identity_fn(_inputs):
        return await _identity(gw, lex, it.metric, filters, primary, baseline)

    tasks: list[Task] = [Task("identity", "Volume vs value", identity_fn, kind="identity"), *[scan_task(d) for d in dims]]
    scan_ids = tuple(f"scan:{d}" for d in dims)

    if depth == "full":
        async def drill_fn(inputs):
            scans_ = sorted([inputs[i] for i in scan_ids if inputs.get(i)], key=lambda s: (-s.localisation, s.n_groups))
            if not (scans_ and scans_[0].movers and scans_[0].gross):
                return None
            top = scans_[0].movers[0]
            if abs(top["delta"]) / scans_[0].gross < 0.2:
                return None
            f2 = {**filters, scans_[0].dim: [str(top["group"])]}
            dims2 = [d for d in candidate_dims(lex, it.metric, f2) if d != scans_[0].dim]
            s2 = await asyncio.gather(*(scan_dimension(gw, it.metric, mi.additive, d, f2, primary, baseline) for d in dims2))
            s2 = sorted([s for s in s2 if s], key=lambda s: (-s.localisation, s.n_groups))
            return DrillResult(scans_[0].dim, str(top["group"]), s2[0]) if s2 else None

        async def confounders_fn(inputs):
            ident = inputs.get("identity")
            skip = {f["metric"] for f in ([ident["volume"], ident["rate"]] if ident else [])}
            return await _confounders(gw, lex, it.metric, filters, primary, baseline, skip)

        async def seasonality_fn(_inputs):
            return await _seasonality(gw, it.metric, filters, primary, baseline)

        async def trend_fn(_inputs):
            return await _trend_context(gw, it.metric, filters, primary)

        async def rigor_fn(inputs):
            res.identity = inputs.get("identity")
            res.scans = sorted([inputs[i] for i in scan_ids if inputs.get(i)], key=lambda s: (-s.localisation, s.n_groups))
            return await build_rigor(gw, res, inputs.get("trend"))

        tasks += [Task("drill", "Drilling into the biggest driver", drill_fn, deps=scan_ids, kind="drill"),
                  Task("confounders", "Related metrics", confounders_fn, deps=("identity",), kind="confounders"),
                  Task("seasonality", "Same period last year", seasonality_fn, kind="seasonality"),
                  Task("trend", "The 12-month trend", trend_fn, kind="trend"),
                  Task("rigor", "Reality checks", rigor_fn, deps=(*scan_ids, "identity", "trend"), kind="rigor")]

    plan = None
    async for kind, payload in run_plan(tasks, max_parallel=6):
        if kind == "step":
            yield "step", payload
        else:
            plan = payload
    res.identity = plan.value("identity")
    scans = [plan.value(i) for i in scan_ids if plan.value(i)]
    scans.sort(key=lambda s: (-s.localisation, s.n_groups))
    res.scans = scans
    if scans:
        yield "step", {"title": "Where it happened", "detail": f"most concentrated by {scans[0].dim}"}

    if depth == "compare":
        res.evidence_ids = list(gw.evidence_ids)
        yield "result", res
        return

    res.drill = plan.value("drill")
    res.confounders = plan.value("confounders") or []
    seas, trend = plan.value("seasonality"), plan.value("trend")
    if seas:
        pt = seas.get("ly_transition_pct")
        seas["seasonal"] = bool(pt is not None and pct is not None and pt * pct > 0 and abs(pt) >= max(2.0, 0.5 * abs(pct)))
    res.seasonality = seas
    res.trend = trend
    res.rigor = plan.value("rigor")
    if res.rigor:
        for c in res.rigor.checks:
            if not c["ok"]:
                caveats.append(f"Check: {c['detail']}.")
    for tid in plan.failed:
        caveats.append(f"One part of the analysis could not be completed ({tid}); the answer covers the rest.")
    if trend:
        fm = trend.get("first_month")
        if fm and _d(primary.start) < _d(fm):
            caveats.append(f"Data for this metric starts in {fm[:7]}.")
        if trend.get("missing_months"):
            caveats.append(f"No data for months: {', '.join(trend['missing_months'][:4])}.")
    res.evidence_ids = list(gw.evidence_ids)
    yield "result", res


# ------------------------------------------------------------------------------------ slice comparison
async def compare_slices(ctx, it: Interp, gw: Gateway) -> AsyncIterator[tuple[str, Any]]:
    lex = ctx.lex
    mi = lex.metrics[it.metric]
    base_filters = dict(it.filters)
    period = it.primary
    notes: list[str] = []
    caveats: list[str] = []
    if period is None:
        f, t = _latest_window(ctx)
        period = make_period(f, t)
        notes.append(f"No period given; used {period.label}.")
    yield "step", {"title": "Measuring each slice", "detail": ", ".join(s.label for s in it.slices)}

    async def measure(sl) -> dict | None:
        merged = {**base_filters, **sl.filters}
        use, dropped = usable_filters(lex, it.metric, merged)
        if dropped:
            caveats.append(f"{mi.title} cannot be filtered by {', '.join(dropped)}; the '{sl.label}' slice ignores it.")
        try:
            r = await gw.query(it.metric, [], use, period)
        except AnalysisError as e:
            caveats.append(f"Slice '{sl.label}': {e}")
            return None
        return {"label": sl.label, "filters": use, "value": r.get("total"), "row_count": r.get("row_count")}

    measured = [m for m in await asyncio.gather(*(measure(s) for s in it.slices)) if m]
    if len(measured) < 2:
        raise AnalysisError("I could not measure at least two slices.")
    vals = [m["value"] for m in measured]
    total = sum(v for v in vals if v is not None) if mi.additive else None
    for m in measured:
        m["share"] = round(100 * m["value"] / total, 1) if total and m["value"] is not None else None
    a, b = measured[0], measured[1]
    gap = None if a["value"] is None or b["value"] is None else a["value"] - b["value"]
    gap_pct = round(100 * gap / abs(b["value"]), 1) if gap is not None and b["value"] else None
    ratio = round(a["value"] / b["value"], 3) if a["value"] is not None and b["value"] else None
    leader = None  # label of the slice with the HIGHER value (whether higher is better depends on the metric)
    if gap is not None and gap != 0:
        leader = max(measured, key=lambda m: m["value"] if m["value"] is not None else float("-inf"))["label"]

    # identity factors per slice
    identity = None
    ident = next((i for i in lex.identities if i["metric"] == it.metric), None)
    if ident and len(ident.get("factors", [])) >= 2:
        f1, f2 = ident["factors"][:2]

        async def factors(m: dict) -> dict | None:
            try:
                r1, r2 = await asyncio.gather(gw.query(f1, [], m["filters"], period), gw.query(f2, [], m["filters"], period))
                return {"label": m["label"], f1: r1.get("total"), f2: r2.get("total")}
            except AnalysisError:
                return None

        fac = [f for f in await asyncio.gather(*(factors(m) for m in measured)) if f]
        if len(fac) == len(measured):
            identity = {"volume": lex.metrics[f1].title, "rate": lex.metrics[f2].title, "volume_id": f1, "rate_id": f2, "rows": fac}

    # related metrics per slice: where do the slices differ?
    differences: list[dict] = []
    related = [m for m in lex.related.get(it.metric, []) if m in lex.metrics and m != it.metric]
    if identity:
        related = [m for m in related if m not in (identity["volume_id"], identity["rate_id"])]

    async def related_for(mid: str) -> dict | None:
        rm = lex.metrics[mid]
        out = []
        for m in measured:
            use, _ = usable_filters(lex, mid, {**base_filters, **next(s.filters for s in it.slices if s.label == m["label"])})
            try:
                r = await gw.query(mid, [], use, period)
            except AnalysisError:
                return None
            out.append({"label": m["label"], "value": r.get("total")})
        if any(o["value"] is None for o in out):
            return None
        hi, lo = max(o["value"] for o in out), min(o["value"] for o in out)
        rel = 100 * (hi - lo) / abs(lo) if lo else None
        return {"metric": mid, "title": rm.title, "unit": rm.unit, "values": out, "relative_gap_pct": rel,
                "material": rel is not None and rel >= lex.materiality_pct, "higher_is_better": rm.higher_is_better}

    yield "step", {"title": "Looking for other differences", "detail": ""}
    rel = [r for r in await asyncio.gather(*(related_for(m) for m in related)) if r]
    differences = sorted([r for r in rel if r["material"]], key=lambda r: -(r["relative_gap_pct"] or 0))

    # mix: top groups of the most informative other dimension, per slice
    mix_dim = None
    if mi.additive:
        used = {k for s in it.slices for k in s.filters}
        for d in ("product_category", "customer_state", "payment_type"):
            if d in lex.dims_of_metric(it.metric) and d not in used and d not in base_filters:
                mix_dim = d
                break
    if mix_dim:
        async def top_groups(m: dict) -> None:
            try:
                r = await gw.query(it.metric, [mix_dim], m["filters"], period, limit=3)
                m["top_groups"] = [{"group": x[mix_dim], "value": x["value"], "share_pct": x.get("share_pct")} for x in r["rows"]]
            except AnalysisError:
                m["top_groups"] = []

        await asyncio.gather(*(top_groups(m) for m in measured))
    eid = await gw.evidence("comparison", it.metric, {"rows": [{k: v for k, v in m.items() if k != "top_groups"} for m in measured],
                                                      "period": period.label, "identity": identity,
                                                      "view": gw.view_for(it.metric, [], base_filters, period)})
    for m in measured:
        m["evidence_id"] = eid
    yield "result", SliceCompareResult(it.metric, mi.title, mi.unit, mi.additive, period, measured, gap, gap_pct, ratio,
                                       leader, identity, differences, mix_dim, notes, caveats, list(gw.evidence_ids))


def _latest_window(ctx) -> tuple[str, str]:
    from contracts.dates import resolve
    return resolve("this_month", ctx.as_of)


# ------------------------------------------------------------------------------------ health scan
async def health_scan(ctx, it: Interp, gw: Gateway) -> AsyncIterator[tuple[str, Any]]:
    lex = ctx.lex
    primary, baseline, notes = default_periods(it.primary, it.baseline, ctx.as_of)
    primary, baseline, n2 = align_periods(primary, baseline, ctx.as_of)
    notes += n2
    yield "step", {"title": "Scanning the main metrics", "detail": f"{primary.label} vs {baseline.label}"}
    ids = [m for m in lex.health_metrics if m in lex.metrics]

    async def one(mid: str) -> dict | None:
        mi = lex.metrics[mid]
        use, _ = usable_filters(lex, mid, it.filters)
        try:
            r, tr = await asyncio.gather(gw.compare(mid, None, use, primary, baseline),
                                         _trend_context(gw, mid, use, primary, record=False))
        except AnalysisError:
            return None
        cur, prev, delta, pct = r["current"]["value"], r["previous"]["value"], r.get("delta"), r.get("pct_change")
        if cur is None or prev is None or delta is None:
            return None
        adverse = (delta < 0 and mi.higher_is_better) or (delta > 0 and not mi.higher_is_better)
        material = pct is not None and abs(pct) >= lex.materiality_pct
        z = (tr or {}).get("z_latest")
        return {"metric": mid, "title": mi.title, "unit": mi.unit, "current": cur, "previous": prev, "delta": delta,
                "pct": pct, "adverse": bool(adverse) and delta != 0, "material": material,
                "streak": (tr or {}).get("streak", 0), "streak_dir": (tr or {}).get("streak_dir", 0),
                "z": z, "outlier": z is not None and abs(z) >= 2.0, "trend": (tr or {}).get("trend")}

    items = [i for i in await asyncio.gather(*(one(m) for m in ids)) if i]
    if not items:
        raise AnalysisError("No metric had data for the two periods.")
    eid = await gw.evidence("comparison", "health_scan", {"rows": items, "primary": primary.label, "baseline": baseline.label})
    for i in items:
        i["evidence_id"] = eid
    yield "result", HealthResult(primary, baseline, items, notes, list(gw.evidence_ids))
