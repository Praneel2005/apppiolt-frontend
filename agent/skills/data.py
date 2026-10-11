"""Data skill: lookups, breakdowns, rankings and trends from the governed metrics, with evidence and a generated view.

Everything is computed by /api/metrics/*; the answer is composed from facts. After answering, the page that shows exactly the
same numbers (when one exists) is opened with the same filters and period; otherwise a chart/table canvas is generated.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from agent.answer import compose_breakdown, compose_single, compose_trend, desc_filters, time_key
from agent.reasoning.engine import trend_summary
from agent.reasoning.facts import FactSheet
from agent.reasoning.gateway import AnalysisError, Gateway, align_periods, previous_of, usable_filters
from agent.reasoning.proof import build_claims
from agent.schema import Interp
from agent.skills.base import SkillResult, emit_canvas, open_supporting_view, reset_canvases, screen_sentence

logger = logging.getLogger("agent.skills.data")


async def run_data(ctx, it: Interp, out: SkillResult) -> AsyncIterator[tuple[str, dict]]:
    lex = ctx.lex
    if not it.metric:
        out.text, out.status, out.kind = "Which metric do you mean?", "INFO", "data"
        return
    gw = Gateway(ctx)
    fs = FactSheet()
    reset_canvases(ctx)
    label = it.primary.label if it.primary else "all available data"
    sections: list[str] = []
    notes: list[str] = []
    nav = None
    first_view = None
    canvas_done = False
    failed = False

    for idx, mid in enumerate([it.metric, *it.extra_metrics][:3]):
        mi = lex.metrics[mid]
        use, dropped = usable_filters(lex, mid, it.filters)
        if dropped:
            notes.append(f"{mi.title} cannot be filtered by {', '.join(d.replace('_', ' ') for d in dropped)}; not applied.")
        ok_dims = set(lex.dims_of_metric(mid))
        gb = [g for g in it.group_by if g in ok_dims]
        shape = it.shape
        if shape in ("single", "none", "list") and not gb:
            multi = [k for k, v in use.items() if len(v) >= 2]
            if multi:
                gb, shape = [multi[0]], "breakdown"
                notes.append(f"You named several {multi[0].replace('_', ' ')} values, so I show them side by side.")
        if shape in ("breakdown", "rank") and not gb:
            shape = "single"
        yield "analysis_step", {"title": f"Computing {mi.title}", "detail": f"{label}" + (f", by {', '.join(gb)}" if gb else "")}
        try:
            if shape == "trend":
                try:
                    tr = await gw.trend(mid, use, it.primary)
                except AnalysisError as e:
                    sections.append(f"A trend needs at least three months of data in the period ({e}).")
                    continue
                summ = trend_summary(tr)
                tk = time_key(tr)
                view = gw.view_for(mid, [tk], use, None)
                ev = await gw.evidence("trend", mid, {**tr, "view": view})
                sections.append(compose_trend(tr, summ, fs, ev, label, use))
                if idx == 0:
                    first_view = view
                if not (idx == 0 and ctx.cfg.open_supporting_view and view and view.get("exact")):
                    await emit_canvas(ctx, "chart", f"{mi.title} over time", tr["rows"], x=tk, y=["value"],
                                      chart_type="line_chart", evidence_ids=[ev], layers=tr.get("source", {}).get("data_layers"))
                    canvas_done = True
            elif shape in ("breakdown", "rank"):
                asc = it.sort_dir == "asc"
                limit = 200 if shape == "breakdown" else max(it.top_n or 5, 10)
                res = await gw.query(mid, gb, use, it.primary, sort="value" if asc else "-value", limit=limit)
                combo = " / ".join(gb)
                for r in res["rows"]:
                    r[combo] = " / ".join(str(r[d]) for d in gb)
                view = gw.view_for(mid, gb, use, it.primary)
                ev = await gw.evidence("metric_query", mid, {**res, "view": view})
                top = it.top_n if shape == "rank" else None
                sections.append(compose_breakdown(res, combo, fs, ev, label, top, asc))
                if idx == 0:
                    first_view = view
                if not (idx == 0 and ctx.cfg.open_supporting_view and view and view.get("exact")):
                    shown = res["rows"][: (top or 10)]
                    time_axis = any(g in lex.time_dims for g in gb)
                    await emit_canvas(ctx, "chart", f"{mi.title} by {', '.join(g.replace('_', ' ') for g in gb)}", shown, x=combo,
                                      y=["value"], chart_type="line_chart" if time_axis else "bar_chart",
                                      evidence_ids=[ev], layers=res.get("source", {}).get("data_layers"),
                                      columns=[combo, "value", *(["share_pct"] if "share_pct" in shown[0] else [])] if shown else None)
                    canvas_done = True
            else:
                res = await gw.query(mid, [], use, it.primary)
                cmp = None
                if it.primary:
                    try:
                        prev = previous_of(it.primary)
                        p2, b2, _ = align_periods(it.primary, prev, ctx.as_of)
                        cmp = await gw.compare(mid, None, use, p2, b2)
                    except AnalysisError:
                        cmp = None
                view = gw.view_for(mid, [], use, it.primary)
                ev = await gw.evidence("metric_query", mid, {**res, "view": view})
                cmp_ev = None
                if cmp:
                    cmp_ev = await gw.evidence("comparison", mid, {**cmp, "rows": [
                        {"period": f"{cmp['previous']['from']} to {cmp['previous']['to']}", "value": cmp["previous"]["value"]},
                        {"period": f"{cmp['current']['from']} to {cmp['current']['to']}", "value": cmp["current"]["value"]}]})
                sections.append(compose_single(res, cmp, fs, ev, label, cmp_ev))
                if idx == 0:
                    first_view = view
        except AnalysisError as e:
            failed = True
            sections.append(f"I could not compute {mi.title.lower()}: {e}")

    out.kind = "data"
    out.steps = len(sections)
    out.citations = list(gw.evidence_ids)
    out.facts = fs
    if first_view and first_view.get("url"):
        out.deep_links.append(first_view["url"])

    if ctx.cfg.open_supporting_view and first_view and first_view.get("exact") and not failed:
        yield "analysis_step", {"title": f"Opening {first_view.get('title')}", "detail": "the page that shows these numbers"}
        nav = await open_supporting_view(ctx, first_view)
        out.screen_note = screen_sentence(nav)

    joined = "\n\n".join(sections)
    text = fs.render(joined)
    out.claims = build_claims(fs, joined, ctx.session)
    if notes:
        text += "\n\n**Notes:** " + " ".join(notes)
    out.text = text
    out.extra_text = notes
    out.mismatches = nav["mismatches"] if nav else []
    if failed and not any("**" in s for s in sections):
        out.status = "FAILED"
    elif notes or (nav and not nav["verified"]):
        out.status = "PARTIAL"
    else:
        out.status = "VERIFIED"
    mi0 = lex.metrics[it.metric]
    alt_dim = next((d for d in lex.filterable_dims(it.metric) if d not in it.group_by and d not in it.filters
                    and d not in lex.time_dims), None)
    out.suggestions = [f"Why did {mi0.title.lower()} change?", f"Show the trend of {mi0.title.lower()}"] + \
                      ([f"Break {mi0.title.lower()} down by {alt_dim.replace('_', ' ')}"] if alt_dim and not it.group_by else [])
