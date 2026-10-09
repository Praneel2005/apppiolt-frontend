"""Executor: runs a validated Plan step by step against the session, the browser and the APIs.

    run_plan(session, plan, config) -> {"ok", "results": [StepResult], ...}

Everything it does is announced as AgentEvents (plan, validation, action, confirm_request, verify,
evidence, canvas, done) so any UI - or a test - can follow along. Policies enforced here:
  * UI-state steps (navigate, filters, date, sort) push an ApplyState and are VERIFIED against the
    browser's RenderAck; with confirm_mode they also need the user's approval first.
  * write_api ALWAYS runs a dry-run preview, asks the user to confirm, and only then applies it;
    the applied action id is kept so it can be undone. Declined = nothing is written.
  * Every number-bearing result becomes Evidence with an id (E1, E2, ...) the answer must cite.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from backend import retrieval
from backend.common import UNTRUSTED_NOTE, ApiError, sanitize_rows
from backend.config import app_model
from backend.deeplinks import state_to_url, supporting_view
from backend.routes.metrics import (MetricCompare, MetricQuery, TrendQuery, metrics_compare, metrics_query,
                                    metrics_trend)
from backend.routes.system import catalog_entries
from backend.sessions import (Session, ask_confirmation, emit, push_state, state_for_page, undo_state)
from backend.validator import validate
from backend.views import ViewError, call_internal, widget_data
from contracts.actions import SIDE_EFFECT, AgentConfig, Evidence, Plan, StepResult, ToolCall
from contracts.canvas import CanvasSpec, FormSpec
from contracts.metadata import Column
from contracts.ui_state import DateRange, FilterValue, Sort

MAX_EVIDENCE_ROWS = 200


def _catalog() -> dict[str, dict]:
    from backend.main import app as asgi_app

    return {e["api_id"]: e for e in catalog_entries(asgi_app)}


def _split(entry: dict, params: dict) -> tuple[str, dict]:
    path, query = entry["path"], {}
    for k, v in params.items():
        if "{" + k + "}" in path:
            path = path.replace("{" + k + "}", str(v))
        else:
            query[k] = v
    return path, query


def _trim(body: Any) -> Any:
    """Keep evidence small: cap long lists."""
    if isinstance(body, dict):
        return {k: _trim(v) for k, v in body.items()}
    if isinstance(body, list) and len(body) > MAX_EVIDENCE_ROWS:
        return body[:MAX_EVIDENCE_ROWS]
    return body


async def _evidence(s: Session, kind: str, source: str, values: dict, query: str | None = None) -> Evidence:
    if isinstance(values.get("rows"), list):
        values["rows"], flagged = sanitize_rows(values["rows"])
        if flagged:
            values["untrusted_fields"], values["untrusted_note"] = flagged, UNTRUSTED_NOTE
    ev = Evidence(evidence_id=s.next_evidence_id(), kind=kind, source=source, query=query, values=values)
    s.evidence[ev.evidence_id] = ev
    await emit(s, "evidence", ev.model_dump())
    return ev


def _rows_of(body: dict) -> list[dict]:
    for key in ("items", "rows", "drivers"):
        if isinstance(body.get(key), list):
            return body[key][:MAX_EVIDENCE_ROWS]
    return []


def _check_expect(s: Session, expect: dict) -> str | None:
    if not expect:
        return None
    if "route" in expect and expect["route"] != s.state.route:
        return f"expected route {expect['route']}, screen shows {s.state.route}"
    for fid, vals in (expect.get("filters") or {}).items():
        cur = s.state.filters.get(fid)
        want = sorted(vals if isinstance(vals, list) else [vals])
        if cur is None or sorted(cur.value) != want:
            return f"expected filter {fid}={want}, screen has {cur.value if cur else None}"
    return None


def _summary(step: ToolCall) -> str:
    a = step.args
    return {"navigate": lambda: f"Open page {a.get('page_id')}", "open_deep_link": lambda: f"Open {a.get('route')}",
            "set_filter": lambda: f"Filter {a.get('filter_id')} = {a.get('value')}",
            "clear_filter": lambda: f"Clear filter {a.get('filter_id')}",
            "set_date_range": lambda: f"Set date range {(a.get('date_range') or {}).get('from')} to {(a.get('date_range') or {}).get('to')}",
            "set_sort": lambda: f"Sort by {a.get('field')} {a.get('dir')}"}.get(step.tool, lambda: step.tool)()


# ------------------------------------------------------------------------------------ UI-state steps
async def _ui_step(s: Session, step: ToolCall, i: int, config: AgentConfig, auto: bool) -> StepResult:
    app, a, st = app_model(), step.args, s.state
    if config.confirm_mode:
        if not await ask_confirmation(s, _summary(step), {"tool": step.tool, "args": a, "kind": "ui_state"}, auto):
            return StepResult(step_index=i, ok=False, error_code="denied", message="The user declined this step.")
    if step.tool in ("navigate", "open_deep_link"):
        page = app.page(a["page_id"])
        flt = {k: FilterValue(**v) for k, v in (a.get("filters") or {}).items()}
        rng = DateRange.model_validate(a["date_range"]) if a.get("date_range") else None
        if a.get("keep_state"):  # S1 exception: carry compatible filters and the date range over
            ids = {f.filter_id for f in page.filters}
            flt = {**{k: v for k, v in st.filters.items() if k in ids}, **flt}
            if rng is None and st.date_range and any(f.type == "date_range" for f in page.filters):
                rng = st.date_range
        sort = Sort(**a["sort"]) if a.get("sort") else None
        new = state_for_page(app, page, flt, rng, sort)
    elif step.tool == "set_filter":
        new = st.model_copy(update={"filters": {**st.filters, a["filter_id"]: FilterValue(op=a["op"], value=a["value"])}})
    elif step.tool == "clear_filter":
        keep = {} if a["filter_id"] == "all" else {k: v for k, v in st.filters.items() if k != a["filter_id"]}
        new = st.model_copy(update={"filters": keep})
    elif step.tool == "set_date_range":
        new = st.model_copy(update={"date_range": DateRange.model_validate(a["date_range"])})
    else:  # set_sort
        new = st.model_copy(update={"sort": Sort(field=a["field"], dir=a["dir"])})
    r = await push_state(s, new, f"{step.tool}: {_summary(step)}")
    v = r["verify"]
    await emit(s, "verify", {"ok": v.ok, "mismatches": v.mismatches, "source": r["source"], "route": s.state.route,
                             "version": s.state.version})
    if config.use_verifier and not v.ok:
        return StepResult(step_index=i, ok=False, error_code="state_mismatch",
                          message=f"The screen does not match the requested state: {v.mismatches[:3]}")
    if (msg := _check_expect(s, step.expect)):
        return StepResult(step_index=i, ok=False, error_code="state_mismatch", message=msg)
    return StepResult(step_index=i, ok=True)


# ------------------------------------------------------------------------------------ read / analysis steps
async def _read_view(s: Session, step: ToolCall, i: int) -> StepResult:
    a, st = step.args, s.state
    d = await widget_data(a["widget_id"], st.filters, st.date_range, st.sort, st.page_id)
    w = app_model().widget(a["widget_id"])
    n = a.get("max_rows", 20)
    ev = await _evidence(s, "widget_read", a["widget_id"], {
        "widget_id": a["widget_id"], "widget_code": w.widget_code, "title": w.title, "columns": d["columns"],
        "rows": d["rows"][:n], "row_count": d["row_count"], "total": d.get("total"), "truncated": d["row_count"] > n,
        "applied_filters": {k: v.model_dump() for k, v in d["applied_filters"].items()},
        "date_range": st.date_range.model_dump(by_alias=True) if st.date_range else None,
        "source": d.get("source"), "view": {"page_id": st.page_id, "widget_id": a["widget_id"],
                                            "widget_code": w.widget_code, "url": state_to_url(app_model(), st),
                                            "exact": True, "dropped": []}})
    return StepResult(step_index=i, ok=True, evidence_id=ev.evidence_id)


async def _analysis(s: Session, step: ToolCall, i: int) -> StepResult:
    a = {k: v for k, v in step.args.items() if v is not None}
    app = app_model()
    try:
        if step.tool == "run_metric_query":
            res = await asyncio.to_thread(metrics_query, MetricQuery(**a))
            view = supporting_view(app, a["metric"], a.get("group_by"), a.get("filters"), a.get("date_from"), a.get("date_to"))
            ev = await _evidence(s, "metric_query", a["metric"], {**res, "rows": res["rows"], "view": view})
        elif step.tool == "analyze_trend":
            res = await asyncio.to_thread(metrics_trend, TrendQuery(**a))
            view = supporting_view(app, a["metric"], ["order_month"], a.get("filters"), a.get("date_from"), a.get("date_to"))
            ev = await _evidence(s, "trend", a["metric"], {**res, "view": view})
        else:
            res = await asyncio.to_thread(metrics_compare, MetricCompare(**a))
            kind = "contribution" if step.tool == "explain_change" else "comparison"
            rows = res["ratio_decomposition"]["groups"] if res.get("ratio_decomposition") else res["drivers"]
            view = supporting_view(app, a["metric"], [a["group_by"]] if a.get("group_by") else [], a.get("filters"),
                                   a.get("date_from"), a.get("date_to"))
            ev = await _evidence(s, kind, a["metric"], {**res, "rows": rows, "view": view})
    except ApiError as e:
        return StepResult(step_index=i, ok=False, error_code="query_failed", message=e.message)
    return StepResult(step_index=i, ok=True, evidence_id=ev.evidence_id)


async def _search(s: Session, step: ToolCall, i: int) -> StepResult:
    kinds = ("page",) if step.tool == "search_pages" else ("api",)
    res = retrieval.search(step.args["query"], kinds, step.args.get("k", 5))
    ev = await _evidence(s, "search", step.tool, {"query": step.args["query"], "results": res, "rows": res})
    return StepResult(step_index=i, ok=True, evidence_id=ev.evidence_id)


async def _call_api(s: Session, step: ToolCall, i: int) -> StepResult:
    entry = _catalog()[step.args["api_id"]]
    path, query = _split(entry, step.args["params"])
    status, body = await call_internal(entry["method"], path, query, headers={"x-actor": "agent"})
    if status != 200:
        err = body.get("error", {})
        return StepResult(step_index=i, ok=False, error_code="api_error", message=f"{err.get('code')}: {err.get('message')}")
    ev = await _evidence(s, "api_read", entry["api_id"], {"api_id": entry["api_id"], "params": step.args["params"],
                                                          "rows": _rows_of(body), "response": _trim(body)})
    return StepResult(step_index=i, ok=True, evidence_id=ev.evidence_id)


# ------------------------------------------------------------------------------------ writes
async def _write_api(s: Session, step: ToolCall, i: int, auto: bool) -> StepResult:
    entry = _catalog()[step.args["api_id"]]
    path, query = _split(entry, step.args["params"])
    body = step.args.get("body") or {}
    hdr = {"x-actor": "agent"}
    status, preview = await call_internal(entry["method"], path, {**query, "dry_run": True}, body, hdr)
    if status != 200:
        err = preview.get("error", {})
        return StepResult(step_index=i, ok=False, error_code="api_error", message=f"{err.get('code')}: {err.get('message')}")
    approved = await ask_confirmation(
        s, f"{step.args.get('summary') or entry['title']}", {
            "kind": "write", "api_id": entry["api_id"], "request": {"params": step.args["params"], "body": body},
            "preview": preview, "undoable": True, "layers": entry["data_layers"]}, auto)
    if not approved:
        return StepResult(step_index=i, ok=False, error_code="denied", message="The user declined; nothing was saved.")
    status, applied = await call_internal(entry["method"], path, query, body, hdr)
    if status != 200:
        err = applied.get("error", {})
        return StepResult(step_index=i, ok=False, error_code="api_error", message=f"{err.get('code')}: {err.get('message')}")
    s.writes.append(applied["action_id"])
    ev = await _evidence(s, "api_write", entry["api_id"], {"api_id": entry["api_id"], "action_id": applied["action_id"],
                                                           "result": applied["result"], "changes": applied["changes"],
                                                           "rows": [{"action_id": applied["action_id"], **{
                                                               k: v for k, v in applied["result"].items()
                                                               if not isinstance(v, (dict, list))}}]})
    return StepResult(step_index=i, ok=True, evidence_id=ev.evidence_id)


async def undo_last_write(s: Session) -> dict:
    if not s.writes:
        return {"ok": False, "message": "No change made in this session can be undone."}
    code = s.writes[-1]
    status, body = await call_internal("POST", f"/api/actions/{code}/undo", headers={"x-actor": "user"})
    if status == 200:
        s.writes.pop()
        s.stats["writes_undone"] += 1
    await emit(s, "action", {"tool": "undo_write", "args": {"action_id": code}, "status": "done" if status == 200 else "failed",
                             "message": body.get("error", {}).get("message", "") if status != 200 else "reverted"})
    return {"ok": status == 200, "action_id": code, "response": body}


# ------------------------------------------------------------------------------------ canvas
def _infer_columns(rows: list[dict], names: list[str] | None) -> list[Column]:
    first = rows[0] if rows else {}
    names = names or [k for k, v in first.items() if not isinstance(v, (dict, list))]
    cols = []
    for n in names:
        v = next((r.get(n) for r in rows if r.get(n) is not None), None)
        t = "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else "text"
        cols.append(Column(name=n, title=n.replace("_", " ").title(), type=t))
    return cols


async def _canvas(s: Session, step: ToolCall, i: int) -> StepResult:
    a = step.args
    cid = f"CV{len(s.canvases) + 1}"
    if a["kind"] == "form":
        entry = _catalog()[a["api_id"]]
        spec = CanvasSpec(canvas_id=cid, kind="form", title=a["title"], data_layers=entry["data_layers"],
                          form=FormSpec(api_id=entry["api_id"], method=entry["method"], path=entry["path"],
                                        title=entry["title"], body_schema=entry["body_schema"] or {},
                                        defaults=a["defaults"]))
    else:
        ev_id = a.get("evidence_id")
        if ev_id == "@last":
            ev = list(s.evidence.values())[-1] if s.evidence else None
        else:
            ev = s.evidence.get(ev_id)
        if ev is None:
            return StepResult(step_index=i, ok=False, error_code="invalid_reference",
                              message=f"No evidence '{ev_id}' in this session.")
        rows = [r for r in (ev.values.get("rows") or []) if isinstance(r, dict)][:MAX_EVIDENCE_ROWS]
        if not rows:
            return StepResult(step_index=i, ok=False, error_code="empty_result", message="The evidence has no rows to show.")
        cols = _infer_columns(rows, a.get("columns") or None)
        names = [c.name for c in cols]
        chart = None
        if a["kind"] == "chart":
            x = a.get("x") or names[0]
            y = a.get("y") or [c.name for c in cols if c.type == "number" and c.name != x][:2]
            if x not in names or not y or not set(y) <= set(names):
                return StepResult(step_index=i, ok=False, error_code="invalid_reference",
                                  message=f"chart x/y must be columns of the data: {names}")
            from contracts.metadata import ChartSpec
            chart = ChartSpec(x=x, y=y)
        layers = ((ev.values.get("source") or {}).get("data_layers")
                  or (ev.values.get("response") or {}).get("source", {}).get("data_layers") or [])
        spec = CanvasSpec(canvas_id=cid, kind=a["kind"], title=a["title"], columns=cols, rows=rows,
                          chart_type=a.get("chart_type") if a["kind"] == "chart" else None, chart=chart,
                          evidence_ids=[ev.evidence_id], data_layers=layers)
    s.canvases[cid] = spec.model_dump()
    await emit(s, "canvas", s.canvases[cid])
    return StepResult(step_index=i, ok=True)


# ------------------------------------------------------------------------------------ plan runner
async def _execute(s: Session, step: ToolCall, i: int, config: AgentConfig, auto: bool) -> StepResult:
    t = step.tool
    if SIDE_EFFECT[t] == "ui_state":
        return await _ui_step(s, step, i, config, auto)
    if t == "read_view":
        return await _read_view(s, step, i)
    if t in ("run_metric_query", "compare_periods", "explain_change", "analyze_trend"):
        return await _analysis(s, step, i)
    if t in ("search_pages", "search_apis"):
        return await _search(s, step, i)
    if t == "call_api":
        return await _call_api(s, step, i)
    if t == "write_api":
        return await _write_api(s, step, i, auto)
    return await _canvas(s, step, i)


async def run_plan(s: Session, plan: Plan, config: AgentConfig | None = None, auto_confirm: bool = False) -> dict:
    """Validate and execute a plan. `auto_confirm` approves confirmations without a user (tests/evaluation only);
    it is recorded on every confirm_request event."""
    config = config or s.config
    t0 = time.time()
    s.stats["plan_runs"] += 1
    async with s.lock:
        await emit(s, "plan", {"intent": plan.intent.model_dump(), "steps": [st.model_dump() for st in plan.steps]})
        if config.use_validator:
            vr = validate(app_model(), plan, s.state, _catalog())
            await emit(s, "validation", {"ok": vr.ok, "issues": [x.model_dump() for x in vr.issues]})
            if not vr.ok:
                s.stats["validation_failures"] += 1
                for i in vr.issues:
                    s.issue_counts[i.code] = s.issue_counts.get(i.code, 0) + 1
                ms = int((time.time() - t0) * 1000)
                s.runs.append({"elapsed_ms": ms, "steps": 0, "ok": False, "stage": "validation"})
                await emit(s, "done", {"steps": 0, "replans": 0, "elapsed_ms": ms, "ok": False})
                return {"ok": False, "stage": "validation", "issues": [x.model_dump() for x in vr.issues], "results": []}
            plan = vr.normalized_plan
        results: list[StepResult] = []
        for i, step in enumerate(plan.steps):
            await emit(s, "action", {"tool": step.tool, "args": step.args, "status": "running", "step_index": i})
            try:
                res = await _execute(s, step, i, config, auto_confirm)
            except (ViewError, ApiError) as e:
                res = StepResult(step_index=i, ok=False, error_code="query_failed", message=getattr(e, "message", str(e)))
            except Exception as e:  # noqa: BLE001
                res = StepResult(step_index=i, ok=False, error_code="query_failed", message=f"{type(e).__name__}: {e}")
            results.append(res)
            s.stats["agent_steps"] += 1
            await emit(s, "action", {"tool": step.tool, "status": "done" if res.ok else "failed", "step_index": i,
                                     "error_code": res.error_code, "message": res.message, "evidence_id": res.evidence_id})
            if not res.ok:
                break
        ok = bool(results) and all(r.ok for r in results) and len(results) == len(plan.steps)
        ms = int((time.time() - t0) * 1000)
        s.runs.append({"elapsed_ms": ms, "steps": len(results), "ok": ok, "stage": "executed"})
        await emit(s, "done", {"steps": len(results), "replans": 0, "elapsed_ms": ms, "ok": ok})
    return {"ok": ok, "stage": "executed", "results": [r.model_dump() for r in results],
            "state": s.state.model_dump(by_alias=True), "evidence_ids": [r.evidence_id for r in results if r.evidence_id]}
