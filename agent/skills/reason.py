"""Reason skill: explain, compare and investigate.

Runs the analysis engine, composes a deterministic answer from facts, optionally adds an LLM interpretation that is
verified against the facts, draws the supporting chart, and opens the page that shows the best breakdown.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from agent.answer import (allowed_numbers_from, compose_explain, compose_health, compose_slices, synthesize)
from agent.reasoning.engine import (ExplainResult, HealthResult, SliceCompareResult, compare_slices, explain, health_scan)
from agent.reasoning.facts import FactSheet
from agent.reasoning.gateway import AnalysisError, Gateway
from agent.reasoning.proof import build_claims
from agent.schema import Interp
from agent.skills.base import SkillResult, emit_canvas, open_supporting_view, reset_canvases, screen_sentence

logger = logging.getLogger("agent.skills.reason")


async def run_reason(ctx, it: Interp, out: SkillResult) -> AsyncIterator[tuple[str, dict]]:
    lex = ctx.lex
    gw = Gateway(ctx)
    fs = FactSheet()
    reset_canvases(ctx)
    out.kind = "analysis"

    if it.intent == "investigate":
        gen = health_scan(ctx, it, gw)
    elif it.intent == "compare" and it.compare_kind == "slices" and len(it.slices) >= 2 and it.metric:
        gen = compare_slices(ctx, it, gw)
    elif it.intent == "compare" and it.metric:
        gen = explain(ctx, it, gw, depth="compare")
    else:
        gen = explain(ctx, it, gw, depth="full")

    result = None
    try:
        async for kind, payload in gen:
            if kind == "step":
                yield "analysis_step", payload
            else:
                result = payload
    except AnalysisError as e:
        out.text = f"I could not complete the analysis: {e}"
        out.status = "FAILED"
        out.citations = list(gw.evidence_ids)
        return
    if result is None:
        out.text, out.status = "The analysis returned no result.", "FAILED"
        return

    if isinstance(result, ExplainResult):
        template, sugg = compose_explain(result, fs)
        flags = {"premise": result.premise, "direction": result.direction, "seasonal": bool(result.seasonality and result.seasonality.get("seasonal")),
                 "metric_higher_is_better": result.higher_is_better, "depth": result.depth}
        view_metric, view_filters, view_period = result.metric, result.filters, result.primary
        best_dim = result.scans[0].dim if result.scans else None
        await _explain_canvas(ctx, result)
        title_note = result.title
    elif isinstance(result, SliceCompareResult):
        template, sugg = compose_slices(result, fs)
        flags = {"kind": "slice comparison"}
        view_metric, view_filters, view_period, best_dim = result.metric, dict(it.filters), result.period, None
        # slices that differ in ONE dimension can be shown by an existing page: that dimension as the breakdown, its values as the filter
        sdim = _slice_dimension(result)
        if sdim:
            best_dim = sdim[0]
            view_filters = {**view_filters, sdim[0]: sdim[1]}
        shown = bool(sdim and ctx.cfg.open_supporting_view and (gw.view_for(view_metric, [sdim[0]], view_filters, view_period) or {}).get("exact"))
        if not shown:   # no page can show it: generate the chart, for this answer only
            await _slices_canvas(ctx, result)
        title_note = result.title
    else:
        assert isinstance(result, HealthResult)
        template, sugg = compose_health(result, fs)
        flags = {"kind": "health scan"}
        view_metric = view_filters = view_period = best_dim = None
        await _health_canvas(ctx, result)
        title_note = "business health"

    # optional interpretation by the LLM, verified against the facts
    narrative = None
    issues: list[str] = []
    strong = getattr(ctx, "llm_strong", None) or ctx.llm
    if ctx.cfg.use_synthesis and strong is not None:
        yield "analysis_step", {"title": "Writing the interpretation", "detail": "checked against the verified facts"}
        allowed = allowed_numbers_from(ctx.text) | {str(n) for n in range(0, 10)}
        narrative, issues = await synthesize(strong, ctx.text, template, fs, flags, allowed)
    body = fs.render(template)
    if narrative:
        lines = body.split("\n")
        body = "\n".join([lines[0], narrative, *lines[1:]])
        out.narrative_used = True
    text = body

    out.text = text
    out.claims = build_claims(fs, template, ctx.session)
    out.extra_text = [*getattr(result, "notes", []), *getattr(result, "caveats", [])]
    out.citations = list(dict.fromkeys(gw.evidence_ids))
    out.suggestions = sugg
    out.facts = fs
    out.steps = len(out.citations)
    out.status = "VERIFIED"

    # put the app on the page that shows the same numbers
    if ctx.cfg.open_supporting_view and view_metric:
        view = gw.view_for(view_metric, [best_dim] if best_dim else [], view_filters, view_period)
        if view and view.get("url"):
            out.deep_links.append(view["url"])
        if view and view.get("exact"):
            yield "analysis_step", {"title": f"Opening {view.get('title')}", "detail": "the page behind these numbers"}
            nav = await open_supporting_view(ctx, view)
            out.screen_note = screen_sentence(nav)
            if nav and not nav["verified"]:
                out.status = "PARTIAL"
                out.mismatches = nav["mismatches"]
    if issues:
        logger.info("interpretation rejected (%s); deterministic answer used", "; ".join(issues[:3]))


def _slice_dimension(res: SliceCompareResult) -> tuple[str, list[str]] | None:
    """(dimension, values) when every slice differs from the others only in one dimension's value(s)."""
    fl = [s.get("filters") or {} for s in res.slices]
    if len(fl) < 2:
        return None
    keys = {k for f in fl for k in f}
    varying = [k for k in keys if len({tuple(f.get(k, ())) for f in fl}) > 1]
    if len(varying) != 1 or any(k != varying[0] and len({tuple(f.get(k, ())) for f in fl}) > 1 for k in keys):
        return None
    d = varying[0]
    if any(d not in f or not f[d] for f in fl):
        return None
    return d, list(dict.fromkeys(v for f in fl for v in f[d]))


async def _explain_canvas(ctx, res: ExplainResult) -> None:
    if not res.scans:
        return
    sc = res.scans[0]
    rows = [{"group": str(r["group"]), "change": r["delta"]} for r in sorted(sc.rows, key=lambda r: -abs(r["delta"]))[:10]]
    if not rows:
        return
    await emit_canvas(ctx, "chart", f"What moved {res.title.lower()} ({res.baseline.label} → {res.primary.label}), by {sc.dim.replace('_', ' ')}",
                      rows, x="group", y=["change"], chart_type="bar_chart",
                      evidence_ids=[e for e in [sc.evidence_id] if e], layers=["derived"])


async def _slices_canvas(ctx, res: SliceCompareResult) -> None:
    rows = [{"slice": str(s["label"]), "value": s["value"]} for s in res.slices if s.get("value") is not None]
    if rows:
        await emit_canvas(ctx, "chart", f"{res.title}: {' vs '.join(r['slice'] for r in rows)}", rows, x="slice", y=["value"],
                          chart_type="bar_chart", evidence_ids=[e for e in [res.slices[0].get("evidence_id")] if e],
                          layers=["derived"])


async def _health_canvas(ctx, res: HealthResult) -> None:
    rows = [{"metric": i["title"], "previous": i["previous"], "current": i["current"],
             "change_pct": i["pct"]} for i in res.items]
    if rows:
        await emit_canvas(ctx, "table", f"Business health: {res.primary.label} vs {res.baseline.label}", rows,
                          evidence_ids=[res.items[0].get("evidence_id")] if res.items[0].get("evidence_id") else [],
                          layers=["derived"], columns=["metric", "previous", "current", "change_pct"])
