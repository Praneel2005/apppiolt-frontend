"""Deep links: UI state <-> URL, and 'which page shows these numbers'.

URL scheme (the browser's address bar, the agent's links and the evaluation tasks all use it; the web app has a
TypeScript twin, web/src/lib/url.ts, tested against contracts/deeplink_vectors.json):

    <route>?from=2018-07-01&to=2018-07-31&preset=last_month&f.customer_state=RJ,SP&op.<id>=<op>&sort=age_hours:desc

  * from / to    ISO dates (inclusive); preset is informational. A link with only preset=... is resolved against as_of_date.
  * f.<id>       the values of filter <id>, comma-separated, each percent-encoded (a comma inside a value is %2C).
  * op.<id>      only written when the operator differs from the filter's default (multiselect: in, select: eq, ...).
  * sort         field:dir.
  * Missing date -> the page default (shared semantics S1). Parameters are written in a fixed order, so the same
    state always yields the same URL.
"""
from __future__ import annotations

from urllib.parse import quote, unquote

from backend.common import ApiError
from backend.sessions import default_range
from backend.validator import _Ctx, _filter, _range, _sortable
from contracts.metadata import Application, FilterDef, Page
from contracts.ui_state import DateRange, FilterValue, Sort, UiState

_DEFAULT_OP = {"multiselect": "in", "select": "eq", "text": "contains", "number_range": "between"}


def _enc(v: str) -> str:
    return quote(str(v), safe="")


def default_op(f: FilterDef | None) -> str:
    return _DEFAULT_OP.get(f.type, "eq") if f else "in"


def state_to_url(app: Application, state: UiState) -> str:
    page = app.page(state.page_id)
    pairs: list[tuple[str, str]] = []
    if state.date_range:
        pairs += [("from", _enc(state.date_range.from_)), ("to", _enc(state.date_range.to))]
        if state.date_range.preset:
            pairs.append(("preset", _enc(state.date_range.preset)))
    for fid in sorted(state.filters):
        fv = state.filters[fid]
        pairs.append((f"f.{fid}", ",".join(_enc(v) for v in fv.value)))
        fdef = next((f for f in page.filters if f.filter_id == fid), None)
        if fv.op != default_op(fdef):
            pairs.append((f"op.{fid}", fv.op))
    if state.sort:
        pairs.append(("sort", f"{_enc(state.sort.field)}:{state.sort.dir}"))
    return page.route + ("?" + "&".join(f"{k}={v}" for k, v in pairs) if pairs else "")


def _split_url(url: str) -> tuple[str, dict[str, str]]:
    url = url.strip()
    if "://" in url:  # absolute URL: keep path + query only
        url = "/" + url.split("://", 1)[1].partition("/")[2]
    url = url.split("#", 1)[0]
    path, _, query = url.partition("?")
    params: dict[str, str] = {}
    for pair in filter(None, query.split("&")):
        k, _, v = pair.partition("=")
        params[unquote(k)] = v  # values stay encoded: commas separate list items
    return (path.rstrip("/") or "/"), params


def parse_url(app: Application, url: str) -> UiState:
    """Validate a deep link against the metadata and return the UI state it describes."""
    path, params = _split_url(url)
    page = next((p for p in app.pages if p.route == path), None)
    if page is None:
        raise ApiError(404, "unknown_route", f"No page has the route '{path}'.",
                       routes_like=[p.route for p in app.pages if p.route.split("/")[-1] == path.split("/")[-1]][:5])
    c = _Ctx(app, {}, page)
    filters: dict[str, FilterValue] = {}
    date_range: DateRange | None = None
    sort: Sort | None = None
    for key, raw in params.items():
        if key.startswith("f."):
            fid = key[2:]
            values = [unquote(v) for v in raw.split(",") if v != ""]
            fv = _filter(c, page, fid, values, unquote(params.get(f"op.{fid}", "")) or None)
            if fv:
                filters[fid] = FilterValue(**fv)
        elif key == "sort":
            field, _, d = unquote(raw).partition(":")
            if field in _sortable(app, page) and d in ("asc", "desc", ""):
                sort = Sort(field=field, dir=d or "desc")
            else:
                c.bad("invalid_reference", f"Cannot sort page '{page.title}' by '{field}'.")
        elif key not in ("from", "to", "preset") and not key.startswith("op."):
            c.bad("invalid_reference", f"Unknown link parameter '{key}'.")
    if "from" in params or "to" in params or "preset" in params:
        if not any(f.type == "date_range" for f in page.filters):
            c.bad("invalid_op", f"Page '{page.title}' has no date range.")
        elif "from" in params and "to" in params:
            r = _range(c, {"from": unquote(params["from"]), "to": unquote(params["to"])})
            if r:
                r["preset"] = unquote(params["preset"]) if "preset" in params else None
                date_range = DateRange.model_validate(r)
        elif "from" in params or "to" in params:
            c.bad("invalid_date", "A link needs both 'from' and 'to'.")
        else:
            r = _range(c, {"preset": unquote(params["preset"])})
            date_range = DateRange.model_validate(r) if r else None
    if c.issues:
        raise ApiError(422, "invalid_deeplink", c.issues[0].message,
                       issues=[i.model_dump() for i in c.issues])
    return UiState(route=page.route, page_id=page.page_id, filters=filters,
                   date_range=date_range if date_range is not None else default_range(app, page), sort=sort)


def nav_args_from_url(app: Application, url: str) -> dict:
    """Arguments for a `navigate` step equivalent to opening the link."""
    st = parse_url(app, url)
    return {"page_id": st.page_id, "route": st.route,
            "filters": {k: v.model_dump() for k, v in st.filters.items()},
            "date_range": st.date_range.model_dump(by_alias=True) if st.date_range else None,
            "sort": st.sort.model_dump() if st.sort else None, "keep_state": False}


# ------------------------------------------------------------------------------------ supporting views
def supporting_view(app: Application, metric: str, group_by: list[str] | None, filters: dict[str, list[str]] | None,
                    date_from: str | None, date_to: str | None) -> dict | None:
    """The page + widget that shows the same metric split the same way, with a link that opens it under the
    same filters and period. `exact` is false when some filter or date could not be carried over."""
    dims = list(group_by or [])
    best: tuple[int, Page, str] | None = None
    for p in app.pages:
        for wid in p.widgets:
            w = app.widget(wid)
            if w.source or metric not in w.metrics or set(w.dimensions) != set(dims):
                continue
            if w.type not in (("kpi_card",) if not dims else ("bar_chart", "line_chart")):
                continue
            # prefer: report pages, a dedicated page over an overview or cross-tab page, single-metric widgets
            rank = (0 if p.kind == "report" else 4) + (2 if dims and p.page_id.endswith(".overview") else 0) \
                + (1 if "_and_" in p.page_id else 0) + (0 if len(w.metrics) == 1 else 1)
            if best is None or rank < best[0]:
                best = (rank, p, wid)
    if best is None:
        return None
    _, page, wid = best
    fdefs = {f.filter_id: f for f in page.filters}
    use: dict[str, FilterValue] = {}
    dropped: list[str] = []
    for fld, vals in (filters or {}).items():
        f = fdefs.get(fld)
        if f and f.allowed_values and set(vals) <= set(f.allowed_values):
            use[fld] = FilterValue(op=default_op(f), value=list(vals))
        else:
            dropped.append(fld)
    rng = None
    if date_from and date_to:
        if "date_range" in fdefs:
            rng = DateRange.model_validate({"from": date_from, "to": date_to, "preset": None})
        else:
            dropped.append("date_range")
    state = UiState(route=page.route, page_id=page.page_id, filters=use,
                    date_range=rng if rng is not None else default_range(app, page))
    return {"page_id": page.page_id, "page_code": page.page_code, "title": page.title, "route": page.route,
            "widget_id": wid, "widget_code": app.widget(wid).widget_code, "url": state_to_url(app, state),
            "exact": not dropped, "dropped": dropped}
