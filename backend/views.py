"""View engine: (widget, UI state) -> rows. The ONE place that decides what a widget shows.

Used by POST /api/widget-data (the browser), by the verifier (what the screen SHOULD show), by the
page-context endpoint (what the agent can see) and by the agent's read_view tool.

Two widget kinds (contracts/metadata.py):
  metric widget : dataset + metrics + dimensions, SQL from the semantic layer (shared semantics S1-S9)
  API widget    : bound to a catalogue API; page filters / date range / sort become that API's parameters (S10)
A filter applies to a widget iff its field is in the widget's dataset (metric widgets) or its filter_id is
listed in source.filter_params (API widgets). Filters that do not apply are not reported as applied.
"""
from __future__ import annotations

from typing import Any

import httpx
from psycopg import sql

from backend.common import jsonable
from backend.config import app_model
from backend.db import ro
from contracts.metadata import Application, Page, Widget
from contracts.ui_state import DateRange, FilterValue, Sort, UiState, canonical_series_hash

MAX_ROWS = 1000


class ViewError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


def page_for_widget(app: Application, widget_id: str, page_id: str | None = None) -> Page:
    if page_id:
        p = app.page(page_id)
        if p and widget_id in p.widgets:
            return p
    for p in app.pages:
        if widget_id in p.widgets:
            return p
    raise ViewError("unknown_widget", f"No page contains widget '{widget_id}'.")


# ------------------------------------------------------------------------------------ internal API calls
def _asgi_client() -> httpx.AsyncClient:
    from backend.main import app as asgi_app  # late import: main imports the routes that import this module

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://internal", timeout=30)


async def call_internal(method: str, path: str, params: dict | None = None, json: Any = None,
                        headers: dict | None = None) -> tuple[int, dict]:
    """Call one of our own catalogue APIs in-process (same validation, same database roles as a real client)."""
    clean = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in (params or {}).items() if v is not None}
    async with _asgi_client() as c:
        r = await c.request(method, path, params=clean, json=json, headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = {"error": {"code": "bad_response", "message": r.text[:300]}}
    return r.status_code, body


# ------------------------------------------------------------------------------------ metric widgets
def _order(w: Widget, cols: list[str], sort: Sort | None) -> sql.Composable:
    if sort and sort.field in cols:
        return sql.SQL(" ORDER BY {} {}").format(sql.Identifier(sort.field), sql.SQL(sort.dir.upper()))
    if "order_month" in w.dimensions:
        return sql.SQL(" ORDER BY order_month ASC")
    if w.metrics and w.dimensions:
        return sql.SQL(" ORDER BY {} DESC").format(sql.Identifier(w.metrics[0]))
    return sql.SQL("")


def _metric_widget(app: Application, page: Page, w: Widget, filters: dict[str, FilterValue],
                   date_range: DateRange | None, sort: Sort | None) -> dict:
    ds = app.dataset(w.dataset_id)
    fields = {f.name for f in ds.fields}
    metrics = {m.metric_id: m for m in ds.metrics}
    cols = list(w.dimensions) + list(w.metrics)
    parts: list[sql.Composable] = []
    params: dict[str, Any] = {}
    applied: dict[str, FilterValue] = {}
    for i, (fid, fv) in enumerate(sorted(filters.items())):
        fdef = next((f for f in page.filters if f.filter_id == fid), None)
        field = fdef.field if fdef else fid
        if field in fields and fv.value:
            parts.append(sql.SQL("{} = ANY({})").format(sql.Identifier(field), sql.Placeholder(f"f{i}")))
            params[f"f{i}"] = list(fv.value)
            applied[fid] = fv
    applied_range = None
    if date_range and ds.time_field and "date_range" in w.supports:
        parts.append(sql.SQL("{} BETWEEN {} AND {}").format(sql.Identifier(ds.time_field), sql.Placeholder("d_from"),
                                                            sql.Placeholder("d_to")))
        params["d_from"], params["d_to"] = date_range.from_, date_range.to
        applied_range = date_range
    select = [sql.Identifier(d) for d in w.dimensions] + [
        sql.SQL("({}) AS {}").format(sql.SQL(metrics[m].sql), sql.Identifier(m)) for m in w.metrics]
    q = sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(select), sql.Identifier(ds.table))
    if parts:
        q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts)
    if w.dimensions:
        q += sql.SQL(" GROUP BY ") + sql.SQL(", ").join(sql.Identifier(d) for d in w.dimensions)
    q += _order(w, cols, sort) + sql.SQL(" LIMIT {}").format(sql.Literal(MAX_ROWS))
    with ro() as conn:
        rows = [jsonable(r) for r in conn.execute(q, params).fetchall()]
    return {"kind": "metric", "columns": cols, "hash_columns": cols, "rows": rows, "row_count": len(rows),
            "applied_filters": applied, "applied_date_range": applied_range,
            "applied_sort": sort if sort and sort.field in cols else None}


# ------------------------------------------------------------------------------------ API widgets
async def _api_widget(page: Page, w: Widget, filters: dict[str, FilterValue], date_range: DateRange | None,
                      sort: Sort | None) -> dict:
    src = w.source
    params: dict[str, Any] = dict(src.params)
    applied: dict[str, FilterValue] = {}
    for fid, param in src.filter_params.items():
        fv = filters.get(fid)
        if fv and fv.value:
            params[param] = ",".join(fv.value)
            applied[fid] = fv
    applied_range = None
    if date_range and src.date_params:
        params[src.date_params["from"]] = date_range.from_
        params[src.date_params["to"]] = date_range.to
        applied_range = date_range
    names = [c.name for c in src.columns]
    applied_sort = None
    if sort and src.sort_param and "sort" in w.supports and sort.field in names:
        params[src.sort_param] = ("-" if sort.dir == "desc" else "") + sort.field
        applied_sort = sort
    api = _catalog_entry(src.api_id)
    status, body = await call_internal(api["method"], api["path"], params)
    if status != 200:
        err = body.get("error", {})
        raise ViewError("api_error", f"{src.api_id}: {err.get('message', status)}", {"status": status, "error": err})
    rows = [{n: r.get(n) for n in names} for r in body[src.items_path]]
    return {"kind": "api", "columns": names, "hash_columns": [c.name for c in src.columns if c.type != "list"],
            "rows": rows, "row_count": len(rows), "total": body.get("total"),
            "applied_filters": applied, "applied_date_range": applied_range, "applied_sort": applied_sort,
            "source": body.get("source")}


_CATALOG: dict[str, dict] = {}


def _catalog_entry(api_id: str) -> dict:
    if not _CATALOG:
        from backend.main import app as asgi_app
        from backend.routes.system import catalog_entries

        _CATALOG.update({e["api_id"]: e for e in catalog_entries(asgi_app)})
    if api_id not in _CATALOG:
        raise ViewError("unknown_api", f"Unknown API '{api_id}'.")
    return _CATALOG[api_id]


# ------------------------------------------------------------------------------------ public
async def widget_data(widget_id: str, filters: dict[str, FilterValue] | None = None,
                      date_range: DateRange | None = None, sort: Sort | None = None,
                      page_id: str | None = None, app: Application | None = None) -> dict:
    app = app or app_model()
    w = app.widget(widget_id)
    if w is None:
        raise ViewError("unknown_widget", f"Unknown widget '{widget_id}'.")
    page = page_for_widget(app, widget_id, page_id)
    filters = filters or {}
    out = (await _api_widget(page, w, filters, date_range, sort)) if w.source else \
        _metric_widget(app, page, w, filters, date_range, sort)
    out["series_hash"] = canonical_series_hash(out["rows"], out["hash_columns"])
    return out


def _dump_filters(fs: dict[str, FilterValue]) -> dict:
    return {k: v.model_dump() for k, v in fs.items()}


async def expected_for(state: UiState, app: Application | None = None) -> dict[str, dict]:
    """What every widget of the state's page SHOULD show, computed independently of the browser."""
    app = app or app_model()
    page = app.page(state.page_id)
    out = {}
    for wid in page.widgets:
        d = await widget_data(wid, state.filters, state.date_range, state.sort, state.page_id, app)
        out[wid] = {"applied_filters": _dump_filters(d["applied_filters"]), "row_count": d["row_count"],
                    "series_hash": d["series_hash"]}
    return out


async def page_context(state: UiState, app: Application | None = None, preview_rows: int = 8) -> dict:
    """Everything the agent may use to resolve 'this', 'here', 'these': the page, the filters, and what each widget shows."""
    app = app or app_model()
    page = app.page(state.page_id)
    widgets = []
    for wid in page.widgets:
        w = app.widget(wid)
        try:
            d = await widget_data(wid, state.filters, state.date_range, state.sort, state.page_id, app)
            shown = {"columns": d["columns"], "row_count": d["row_count"], "total": d.get("total"),
                     "rows": d["rows"][:preview_rows], "truncated": d["row_count"] > preview_rows,
                     "applied_filters": _dump_filters(d["applied_filters"])}
        except ViewError as e:
            shown = {"error": e.message}
        widgets.append({"widget_id": wid, "widget_code": w.widget_code, "type": w.type, "title": w.title,
                        "description": w.description, "bound_to": w.source.api_id if w.source else
                        {"dataset": w.dataset_id, "metrics": w.metrics, "dimensions": w.dimensions},
                        "row_actions": [a.model_dump() for a in w.source.row_actions] if w.source else [],
                        **shown})
    filters = []
    for f in page.filters:
        cur = state.filters.get(f.filter_id)
        filters.append({"filter_id": f.filter_id, "title": f.title, "type": f.type,
                        "value": cur.value if cur else None, "allowed_values": f.allowed_values})
    return {"page": {"page_id": page.page_id, "page_code": page.page_code, "kind": page.kind, "title": page.title,
                     "route": page.route, "description": page.description, "agent_context": page.agent_context,
                     "directory": page.directory},
            "state": state.model_dump(by_alias=True),
            "filters": filters,
            "date_range": state.date_range.model_dump(by_alias=True) if state.date_range else None,
            "sort": state.sort.model_dump() if state.sort else None,
            "selected_widget": state.selected_widget,
            "widgets": widgets,
            "page_actions": [a.model_dump() for a in page.actions],
            "as_of_date": app.as_of_date}
