"""Plan validator: the safety gate between the LLM's proposal and any execution.

Every reference in a plan (page, filter, filter value, date, metric, dimension, API, parameter, body field)
is checked against the application metadata and the live API catalogue. Synonyms are resolved to canonical
values, presets become dates, and the normalized plan is what gets executed. Problems come back as
ValidationIssues with a suggestion (the nearest valid value) so the planner can repair its plan.
Nothing here touches the database or the browser.
"""
from __future__ import annotations

import difflib
from datetime import date
from typing import Any

from backend.common import ApiError, date_range
from backend.routes.metrics import _check_dims, _check_filters, _metric
from contracts.actions import Plan, ToolCall
from contracts.dates import resolve
from contracts.metadata import Application, FilterDef, Page
from contracts.query import ValidationIssue, ValidationResult
from contracts.ui_state import UiState

MAX_WRITES_PER_PLAN = 5


class _Ctx:
    def __init__(self, app: Application, catalog: dict[str, dict], page: Page):
        self.app, self.catalog, self.page = app, catalog, page
        self.issues: list[ValidationIssue] = []
        self.i = 0

    def bad(self, code: str, message: str, suggestion: str | None = None) -> None:
        self.issues.append(ValidationIssue(step_index=self.i, code=code, message=message, suggestion=suggestion))


def _near(raw: str, options: list[str]) -> str | None:
    m = difflib.get_close_matches(str(raw).lower(), [o.lower() for o in options], n=1, cutoff=0.5)
    return next((o for o in options if o.lower() == m[0]), None) if m else None


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    vs = v if isinstance(v, (list, tuple)) else [v]
    return [("true" if x is True else "false" if x is False else str(x)).strip() for x in vs if str(x).strip()]


def _canon(f: FilterDef, raw: str) -> str | None:
    if not f.allowed_values:
        return raw
    low = {a.lower(): a for a in f.allowed_values}
    if raw in f.allowed_values:
        return raw
    if raw.lower() in low:
        return low[raw.lower()]
    for canonical, aliases in f.synonyms.items():
        if raw.lower() in {a.lower() for a in aliases} or raw.lower() == canonical.lower():
            return canonical
    return None


def _filter(c: _Ctx, page: Page, fid: str, raw_values: Any, op: str | None) -> dict | None:
    f = next((x for x in page.filters if x.filter_id == fid), None)
    if f is None:
        c.bad("invalid_reference", f"Page '{page.title}' has no filter '{fid}'.",
              _near(fid, [x.filter_id for x in page.filters]) or f"available: {[x.filter_id for x in page.filters]}")
        return None
    if f.type == "date_range":
        c.bad("invalid_op", f"'{fid}' is a date range; use set_date_range.")
        return None
    vals, out = _as_list(raw_values), []
    if not vals:
        c.bad("invalid_value", f"Filter '{fid}' needs at least one value.")
        return None
    for v in vals:
        canon = _canon(f, v)
        if canon is None:
            c.bad("invalid_value", f"'{v}' is not a valid value for filter '{f.title}'.",
                  _near(v, list(f.allowed_values or []) + [a for al in f.synonyms.values() for a in al]))
            return None
        out.append(canon)
    op = op or ("in" if f.type == "multiselect" else "eq")
    if op not in f.allowed_ops:
        c.bad("invalid_op", f"Filter '{fid}' does not allow '{op}'.", f"allowed: {f.allowed_ops}")
        return None
    if op == "eq" and len(out) > 1:
        c.bad("invalid_value", f"Filter '{fid}' takes one value with 'eq'; got {len(out)}.", "use op 'in'")
        return None
    return {"op": op, "value": out}


def _range(c: _Ctx, a: dict | None) -> dict | None:
    if not a:
        return None
    try:
        if a.get("preset"):
            f, t = resolve(str(a["preset"]), c.app.as_of_date)
            return {"from": f, "to": t, "preset": a["preset"]}
        f, t = date.fromisoformat(str(a.get("from") or a.get("date_from"))), date.fromisoformat(str(a.get("to") or a.get("date_to")))
        if f > t:
            raise ValueError(f"from {f} is after to {t}")
        return {"from": f.isoformat(), "to": t.isoformat(), "preset": None}
    except (ValueError, TypeError) as e:
        c.bad("invalid_date", f"Invalid date range: {e}",
              "use a preset (last_month, last_quarter, month:2018-03, quarter:2018Q2, year:2017) or ISO dates")
        return None


def _page(c: _Ctx, a: dict) -> Page | None:
    app = c.app
    p = (app.page(a["page_id"]) if a.get("page_id") else
         next((x for x in app.pages if x.page_code and x.page_code.upper() == str(a.get("page_code", "")).upper()), None)
         if a.get("page_code") else next((x for x in app.pages if x.route == a.get("route")), None))
    if p is None:
        key = a.get("page_id") or a.get("page_code") or a.get("route")
        c.bad("invalid_reference", f"Unknown page '{key}'.",
              _near(str(key), [x.page_id for x in app.pages]) if key else "give page_id, page_code or route")
    return p


def _sortable(app: Application, page: Page) -> set[str]:
    out: set[str] = set()
    for wid in page.widgets:
        w = app.widget(wid)
        if "sort" not in w.supports:
            continue
        out |= {col.name for col in w.source.columns} if w.source else set(w.dimensions) | set(w.metrics)
    return out


# ------------------------------------------------------------------------------------ catalogue parameter checks
def _types(spec: dict) -> list[str]:
    t = spec.get("type")
    if t is None and "anyOf" in spec:
        t = [x.get("type") for x in spec["anyOf"] if x.get("type") != "null"]
    return t if isinstance(t, list) else [t]


def _coerce(c: _Ctx, name: str, spec: dict, v: Any) -> Any:
    types, enum = _types(spec), spec.get("enum")
    try:
        if "boolean" in types:
            if isinstance(v, bool):
                return v
            if str(v).lower() in ("true", "false"):
                return str(v).lower() == "true"
            raise ValueError("expected true or false")
        if "integer" in types:
            n = int(v)
            if "minimum" in spec and n < spec["minimum"] or "maximum" in spec and n > spec["maximum"]:
                raise ValueError(f"must be between {spec.get('minimum')} and {spec.get('maximum')}")
            return n
        if "number" in types:
            n = float(v)
            if "minimum" in spec and n < spec["minimum"] or "maximum" in spec and n > spec["maximum"]:
                raise ValueError(f"must be between {spec.get('minimum')} and {spec.get('maximum')}")
            return n
        if "array" in types:
            return [str(x) for x in (v if isinstance(v, list) else [v])]
        s = ",".join(map(str, v)) if isinstance(v, list) else str(v)
        if enum:
            parts = [p.strip() for p in s.split(",") if p.strip()]
            canon = []
            for p in parts:
                hit = next((e for e in enum if e == p or str(e).lower() == p.lower()), None)
                if hit is None:
                    c.bad("invalid_value", f"'{p}' is not a valid value for '{name}'.", _near(p, [str(e) for e in enum]))
                    return v
                canon.append(hit)
            return ",".join(canon)
        if name in ("date_from", "date_to", "won_from", "won_to", "start_date", "end_date"):
            date.fromisoformat(s)
        return s
    except (ValueError, TypeError) as e:
        c.bad("invalid_value", f"Parameter '{name}': {e}")
        return v


def _check_api_call(c: _Ctx, a: dict, want: str) -> dict | None:
    api_id = a.get("api_id")
    entry = c.catalog.get(api_id)
    if entry is None:
        c.bad("invalid_reference", f"Unknown API '{api_id}'.", _near(str(api_id), list(c.catalog)))
        return None
    if entry["kind"] != want:
        c.bad("disallowed_tool", f"{api_id} is a {entry['kind']} API; use {'write_api' if entry['kind'] == 'write' else 'call_api'}.")
        return None
    specs = {p["name"]: p for p in entry["parameters"]}
    given = dict(a.get("params") or {})
    unknown = [k for k in given if k not in specs]
    if unknown:
        c.bad("invalid_reference", f"{api_id} has no parameter(s) {unknown}.",
              f"available: {sorted(specs)}" if specs else "it takes no query/path parameters")
    params = {k: _coerce(c, k, specs[k], v) for k, v in given.items() if k in specs}
    missing = [n for n, s in specs.items() if s.get("required") and n not in params]
    if missing:
        c.bad("invalid_value", f"{api_id} needs parameter(s) {missing}.")
    out: dict = {"api_id": api_id, "params": params}
    if want == "write":
        schema = entry["body_schema"] or {}
        props, body = schema.get("properties", {}), dict(a.get("body") or {})
        extra = [k for k in body if k not in props]
        if extra:
            c.bad("invalid_reference", f"{api_id} body has no field(s) {extra}.", f"fields: {sorted(props)}")
        nb = {k: _coerce(c, k, props[k], v) for k, v in body.items() if k in props}
        miss = [k for k in schema.get("required", []) if k not in nb]
        if miss:
            c.bad("invalid_value", f"{api_id} body needs {miss}.")
        out["body"] = nb
        out["summary"] = str(a.get("summary") or entry["title"])
    return out


# ------------------------------------------------------------------------------------ tool handlers
def _h_search(c: _Ctx, a: dict) -> dict | None:
    if not str(a.get("query", "")).strip():
        c.bad("invalid_value", "search needs a non-empty 'query'.")
        return None
    return {"query": str(a["query"]).strip(), "k": int(a.get("k", 5))}


def _h_navigate(c: _Ctx, a: dict) -> dict | None:
    if a.get("url"):  # open_deep_link with a full link: parse and validate it like a navigate step
        from backend.deeplinks import nav_args_from_url

        try:
            a = nav_args_from_url(c.app, str(a["url"]))
        except ApiError as e:
            c.bad("invalid_reference" if e.status == 404 else "invalid_value", e.message,
                  (e.extra.get("routes_like") or [None])[0])
            return None
    p = _page(c, a)
    if p is None:
        return None
    filters = {}
    for fid, spec in (a.get("filters") or {}).items():
        spec = spec if isinstance(spec, dict) else {"value": spec}
        fv = _filter(c, p, fid, spec.get("value", spec.get("values")), spec.get("op"))
        if fv:
            filters[fid] = fv
    rng = _range(c, a.get("date_range"))
    if a.get("date_range") and not any(f.type == "date_range" for f in p.filters):
        c.bad("invalid_op", f"Page '{p.title}' has no date range.")
    c.page = p
    return {"page_id": p.page_id, "route": p.route, "filters": filters, "date_range": rng,
            "sort": a.get("sort"), "keep_state": bool(a.get("keep_state", False))}


def _h_set_filter(c: _Ctx, a: dict) -> dict | None:
    fv = _filter(c, c.page, str(a.get("filter_id")), a.get("values", a.get("value")), a.get("op"))
    return {"filter_id": a["filter_id"], **fv} if fv else None


def _h_clear_filter(c: _Ctx, a: dict) -> dict | None:
    fid = str(a.get("filter_id", ""))
    if fid != "all" and not any(f.filter_id == fid for f in c.page.filters):
        c.bad("invalid_reference", f"Page '{c.page.title}' has no filter '{fid}'.", _near(fid, [f.filter_id for f in c.page.filters]))
        return None
    return {"filter_id": fid}


def _h_set_date(c: _Ctx, a: dict) -> dict | None:
    if not any(f.type == "date_range" for f in c.page.filters):
        c.bad("invalid_op", f"Page '{c.page.title}' has no date range.")
        return None
    r = _range(c, a)
    return {"date_range": r} if r else None


def _h_set_sort(c: _Ctx, a: dict) -> dict | None:
    ok = _sortable(c.app, c.page)
    field = str(a.get("field", ""))
    if field not in ok:
        c.bad("invalid_reference", f"Cannot sort page '{c.page.title}' by '{field}'.", _near(field, sorted(ok)) or f"sortable: {sorted(ok)}")
        return None
    d = a.get("dir", "desc")
    if d not in ("asc", "desc"):
        c.bad("invalid_value", f"dir must be asc or desc, got '{d}'.")
        return None
    return {"field": field, "dir": d}


def _h_read_view(c: _Ctx, a: dict) -> dict | None:
    wid = a.get("widget_id")
    if wid and wid not in c.page.widgets:
        c.bad("invalid_reference", f"Widget '{wid}' is not on page '{c.page.title}'.", _near(str(wid), c.page.widgets))
        return None
    return {"widget_id": wid or c.page.widgets[0], "max_rows": min(int(a.get("max_rows", 20)), 200)}


def _metric_args(c: _Ctx, a: dict, compare: bool, explain: bool) -> dict | None:
    try:
        ds, m = _metric(str(a.get("metric")))
        group = a.get("group_by")
        dims = [group] if isinstance(group, str) and group else list(group or [])
        if compare:
            dims = dims[:1]
        _check_dims(ds, dims)
        flt = _check_filters(ds, {k: _as_list(v) for k, v in (a.get("filters") or {}).items()})
        pre = a.get("preset") or (a.get("period") or {}).get("preset")
        f, t = date_range(date.fromisoformat(a["date_from"]) if a.get("date_from") else None,
                          date.fromisoformat(a["date_to"]) if a.get("date_to") else None, pre)
        if (compare or explain) and not (f and t):
            raise ApiError(422, "period_required", "A period (preset or dates) is required.")
        out = {"metric": m["metric_id"], "group_by": (dims[0] if dims else None) if compare else dims, "filters": flt,
               "date_from": f.isoformat() if f else None, "date_to": t.isoformat() if t else None}
        if explain and (not dims or not (m["additive"] or m.get("weight_sql"))):
            raise ApiError(422, "not_explainable", "explain_change needs group_by and an additive metric "
                                                   "(or a ratio metric with a defined weight).")
        if compare:
            cf, ct = date_range(date.fromisoformat(a["compare_from"]) if a.get("compare_from") else None,
                                date.fromisoformat(a["compare_to"]) if a.get("compare_to") else None,
                                a.get("compare_preset"))
            out.update({"compare_from": cf.isoformat() if cf else None, "compare_to": ct.isoformat() if ct else None})
            out["limit"] = int(a.get("limit", 20))
        else:
            out.update({"sort": a.get("sort"), "limit": int(a.get("limit", 100))})
        return out
    except ApiError as e:
        c.bad("invalid_value" if e.code != "unknown_metric" else "invalid_reference", e.message,
              (e.extra.get("did_you_mean") or [None])[0] if e.extra.get("did_you_mean") else None)
    except (ValueError, TypeError, KeyError) as e:
        c.bad("invalid_date", f"Invalid date: {e}")
    return None


def _h_trend(c: _Ctx, a: dict) -> dict | None:
    try:
        ds, m = _metric(str(a.get("metric")))
        flt = _check_filters(ds, {k: _as_list(v) for k, v in (a.get("filters") or {}).items()})
        f, t = date_range(date.fromisoformat(a["date_from"]) if a.get("date_from") else None,
                          date.fromisoformat(a["date_to"]) if a.get("date_to") else None, a.get("preset"))
        return {"metric": m["metric_id"], "filters": flt, "date_from": f.isoformat() if f else None,
                "date_to": t.isoformat() if t else None}
    except ApiError as e:
        c.bad("invalid_value" if e.code != "unknown_metric" else "invalid_reference", e.message,
              (e.extra.get("did_you_mean") or [None])[0])
    except (ValueError, TypeError) as e:
        c.bad("invalid_date", f"Invalid date: {e}")
    return None


def _h_canvas(c: _Ctx, a: dict) -> dict | None:
    kind = a.get("kind")
    if kind not in ("chart", "table", "form"):
        c.bad("invalid_value", "render_canvas kind must be chart, table or form.")
        return None
    if kind == "form":
        entry = c.catalog.get(a.get("api_id"))
        if entry is None or entry["kind"] != "write":
            c.bad("invalid_reference", f"Form needs a write API; '{a.get('api_id')}' is not one.",
                  _near(str(a.get("api_id")), [k for k, v in c.catalog.items() if v["kind"] == "write"]))
            return None
        return {"kind": "form", "api_id": a["api_id"], "title": str(a.get("title") or entry["title"]),
                "defaults": dict(a.get("defaults") or {})}
    if not a.get("evidence_id"):
        c.bad("invalid_value", f"{kind} canvas needs 'evidence_id' of data fetched earlier in the plan.")
        return None
    if kind == "chart" and a.get("chart_type", "bar_chart") not in ("bar_chart", "line_chart"):
        c.bad("invalid_value", "chart_type must be bar_chart or line_chart.")
        return None
    return {"kind": kind, "evidence_id": a["evidence_id"], "title": str(a.get("title") or "Result"),
            "chart_type": a.get("chart_type", "bar_chart"), "x": a.get("x"), "y": _as_list(a.get("y")),
            "columns": _as_list(a.get("columns"))}


def validate(app: Application, plan: Plan, state: UiState | None, catalog: dict[str, dict]) -> ValidationResult:
    c = _Ctx(app, catalog, app.page(state.page_id) if state else app.pages[0])
    steps: list[ToolCall] = []
    writes = 0
    for i, step in enumerate(plan.steps):
        c.i = i
        a, t = dict(step.args), step.tool
        norm: dict | None
        if t in ("search_pages", "search_apis"):
            norm = _h_search(c, a)
        elif t in ("navigate", "open_deep_link"):
            norm = _h_navigate(c, a)
        elif t == "set_filter":
            norm = _h_set_filter(c, a)
        elif t == "clear_filter":
            norm = _h_clear_filter(c, a)
        elif t == "set_date_range":
            norm = _h_set_date(c, a)
        elif t == "set_sort":
            norm = _h_set_sort(c, a)
        elif t == "read_view":
            norm = _h_read_view(c, a)
        elif t == "run_metric_query":
            norm = _metric_args(c, a, compare=False, explain=False)
        elif t == "compare_periods":
            norm = _metric_args(c, a, compare=True, explain=False)
        elif t == "explain_change":
            norm = _metric_args(c, a, compare=True, explain=True)
        elif t == "analyze_trend":
            norm = _h_trend(c, a)
        elif t == "call_api":
            norm = _check_api_call(c, a, "read")
        elif t == "write_api":
            writes += 1
            if writes > MAX_WRITES_PER_PLAN:
                c.bad("unsafe", f"A plan may contain at most {MAX_WRITES_PER_PLAN} write steps; use a bulk API.")
            norm = _check_api_call(c, a, "write")
        elif t == "render_canvas":
            norm = _h_canvas(c, a)
        else:  # pragma: no cover  (pydantic already restricts ToolName)
            c.bad("disallowed_tool", f"Unknown tool {t}")
            norm = None
        steps.append(ToolCall(tool=t, args=norm if norm is not None else a, expect=step.expect))
    if c.issues:
        return ValidationResult(ok=False, issues=c.issues)
    return ValidationResult(ok=True, normalized_plan=plan.model_copy(update={"steps": steps}), issues=[])
