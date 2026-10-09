"""Verifier: compares what the browser REPORTS it rendered (RenderAck) with what the screen SHOULD show.

The expectation is computed by the view engine from the desired UiState, independently of the browser
and of the code that applied the state, so a browser that drops a filter, shows stale data or fails to
render is caught (non-circular verification, contracts/ui_state.py).
"""
from __future__ import annotations

from backend.views import _dump_filters, expected_for
from contracts.metadata import Application
from contracts.query import VerifyResult
from contracts.ui_state import RenderAck, UiState


async def verify(app: Application, expected: UiState, ack: RenderAck) -> VerifyResult:
    exp = await expected_for(expected, app)
    mism: list[dict] = []
    if ack.route != expected.route:
        mism.append({"widget_id": None, "field": "route", "expected": expected.route, "actual": ack.route})
    if ack.version != expected.version:
        mism.append({"widget_id": None, "field": "version", "expected": expected.version, "actual": ack.version})
    got = {w.widget_id: w for w in ack.widgets}
    for wid, e in exp.items():
        a = got.get(wid)
        if a is None:
            mism.append({"widget_id": wid, "field": "missing_ack", "expected": "ack", "actual": None})
            continue
        if a.error:
            mism.append({"widget_id": wid, "field": "render_error", "expected": None, "actual": a.error})
            continue
        for field, actual in (("applied_filters", _dump_filters(a.applied_filters)), ("row_count", a.row_count),
                              ("series_hash", a.series_hash)):
            if actual != e[field]:
                mism.append({"widget_id": wid, "field": field, "expected": e[field], "actual": actual})
    return VerifyResult(ok=not mism, mismatches=mism)
