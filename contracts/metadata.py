"""Canonical application-metadata schema (Contract 1).

Every other component reads this: the renderer draws pages from it, the tenant
generator emits it, the agent retrieves over it and builds its tool contracts and
validator from it. Foreign exports (Superset, Appsmith, ...) are *importers* that
produce these models; they never reach the agent directly.

Change this file only by agreement of the whole team, and bump SCHEMA_VERSION.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "0.2.0"
# 0.2.0 (additive, 2026-10-09): page_code / widget_code (P-200, R-201), Page.agent_context / kind / layout /
#   actions, Widget.source (binds a widget to a catalogue API instead of a metric query; dataset_id becomes
#   optional), Column, ChartSpec, Action. Everything from 0.1.0 is unchanged.

# Ids are namespaced, lowercase, dot-separated: "sales.revenue_by_region.chart_main"
IdStr = str


class _Model(BaseModel):
    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------- data layer
class FieldDef(_Model):
    name: str
    type: Literal["date", "string", "enum", "integer", "decimal", "boolean"]
    role: Literal["time", "dimension", "metric", "id", "attribute"]
    description: str = ""
    values: list[str] | None = None  # for enums: the complete allowed set


class Metric(_Model):
    """Semantic-layer metric: defined once, used by every widget and query."""

    metric_id: IdStr
    title: str
    description: str
    sql: str  # aggregate expression over the dataset's table, e.g. SUM(revenue)
    unit: str = ""  # "USD", "count", "%"
    higher_is_better: bool = True
    # True for sums/counts (revenue, orders); False for ratios/averages (aov, late_rate).
    # explain_change does contribution decomposition on additive metrics, and a mix/rate decomposition on
    # non-additive ratio metrics that define `weight_sql`.
    additive: bool = True
    # SQL aggregate giving the denominator the ratio is averaged over (e.g. COUNT(is_late) for late_rate), so a
    # change in the ratio can be split exactly into a mix effect (group weights moved) and a rate effect.
    weight_sql: str | None = None


class Dataset(_Model):
    dataset_id: IdStr
    table: str
    description: str
    time_field: str | None = None
    fields: list[FieldDef]
    metrics: list[Metric] = []

    def field(self, name: str) -> FieldDef | None:
        return next((f for f in self.fields if f.name == name), None)


# ------------------------------------------------------------------- filters
class FilterDef(_Model):
    filter_id: IdStr  # unique within a page
    field: str  # dataset field it constrains
    type: Literal["date_range", "select", "multiselect", "text", "number_range"]
    title: str
    allowed_ops: list[Literal["eq", "in", "between", "contains", "gte", "lte"]]
    allowed_values: list[str] | None = None  # closed set -> validator enforces it
    synonyms: dict[str, list[str]] = {}  # canonical value -> aliases ("West": ["western"])


# ------------------------------------------------------------------- widgets
class RenderContract(_Model):
    """What the renderer MUST report back in its ack for this widget (see ui_state.RenderAck).

    This is what makes verification non-circular: the ack is computed from the query
    the widget actually sent and the data it actually displayed.
    """

    query_metrics: list[IdStr]
    query_dimensions: list[str] = []
    ack_fields: list[Literal["applied_filters", "row_count", "series_hash"]] = [
        "applied_filters",
        "row_count",
        "series_hash",
    ]


Scalar = str | int | float | bool | None


class Column(_Model):
    """One column of an API-bound grid: `name` is the key in each row of the API response."""

    name: str
    title: str
    # percent = already 0-100 (show "22.2%"); ratio = 0-1 (show "22.2%" after x100); badge = short status word;
    # list = array of strings; money values are in `unit` (BRL)
    type: Literal["text", "number", "money", "percent", "ratio", "date", "datetime", "badge", "list"] = "text"
    unit: str = ""
    description: str = ""
    hidden: bool = False  # present in every row (actions and the hash use it) but not shown as a column


class ChartSpec(_Model):
    """How a bar/line chart reads the rows of an API-bound widget."""

    x: str  # column used for categories / time
    y: list[str]  # one or more value columns (one series each)
    stacked: bool = False


class Action(_Model):
    """A write the user (or the agent) can trigger from a page or a table row, through a catalogue API.

    The UI shows a confirmation card with the API's dry_run preview before anything is saved; if the
    API has more body fields than `params_from_row` + `fixed` fill, the UI builds a form from the API's
    body_schema (GET /api/catalog). Nothing here is executed without that confirmation.
    """

    action_id: IdStr  # unique within its page, e.g. "open_ticket"
    api_id: IdStr  # catalogue id, e.g. "tickets.create"
    label: str
    description: str = ""
    # request field or path parameter -> row column holding its value (row actions only)
    params_from_row: dict[str, str] = {}
    fixed: dict[str, Scalar] = {}  # constant body fields, e.g. {"status": "resolved"}
    form: bool = False  # true = ask the user for the remaining fields
    style: Literal["default", "danger"] = "default"


class WidgetSource(_Model):
    """Binds a widget to a catalogue API. The backend resolves it server-side, so the browser still calls
    POST /api/widget-data {widget_id, filters, date_range, sort} and receives {columns, rows, row_count}."""

    api_id: IdStr
    params: dict[str, Scalar] = {}  # fixed query parameters, e.g. {"late": True, "limit": 50}
    filter_params: dict[str, str] = {}  # page filter_id -> API parameter (a filter applies iff listed here: S10)
    date_params: dict[str, str] | None = None  # {"from": "date_from", "to": "date_to"} or None
    sort_param: str | None = "sort"  # API sort parameter ("-days_late" style), None if the API has no sort
    items_path: str = "items"  # key of the row list in the API response
    columns: list[Column]
    chart: ChartSpec | None = None
    row_actions: list[Action] = []


class Widget(_Model):
    widget_id: IdStr
    widget_code: str | None = None  # short human id shown in the UI, e.g. "R-201"
    type: Literal["grid", "bar_chart", "line_chart", "pie_chart", "kpi_card", "filter_bar"]
    title: str
    description: str  # written for retrieval: say what question this widget answers
    dataset_id: IdStr | None = None  # metric widgets; None when `source` is set
    metrics: list[IdStr] = []
    dimensions: list[str] = []
    supports: list[Literal["sort", "filter", "date_range", "drilldown"]] = []
    render: RenderContract | None = None
    source: WidgetSource | None = None  # API-bound widgets (orders, tickets, stock, ...)

    @model_validator(mode="after")
    def _one_binding(self) -> "Widget":
        if (self.dataset_id is None) == (self.source is None):
            raise ValueError(f"{self.widget_id}: set exactly one of dataset_id (metric widget) or source (API widget)")
        if self.source is not None and self.type in ("bar_chart", "line_chart") and self.source.chart is None:
            raise ValueError(f"{self.widget_id}: chart widgets bound to an API need source.chart")
        return self


# --------------------------------------------------------------- pages/routes
class Page(_Model):
    page_id: IdStr
    page_code: str | None = None  # short human id shown in the UI, e.g. "P-200"
    kind: Literal["report", "operations"] = "report"
    title: str
    description: str  # written for retrieval
    # What the agent should know when the user is on this page: what it is for, what "this"/"here" usually
    # refers to, which entities it shows, what the user typically does next. Fed to the agent as page context.
    agent_context: str = ""
    directory: str  # navigation hierarchy path, e.g. "sales/revenue"
    route: str  # unique, e.g. "/sales/revenue-by-region"
    keywords: list[str] = []
    widgets: list[IdStr]
    layout: list[list[IdStr]] | None = None  # rows of widget ids, left to right; None = renderer decides
    filters: list[FilterDef] = []
    default_state: dict = {}
    actions: list[Action] = []  # page-level writes, e.g. "New promotion"

    @model_validator(mode="after")
    def _route_shape(self) -> "Page":
        if not self.route.startswith("/"):
            raise ValueError(f"route must start with '/': {self.route}")
        return self


class Directory(_Model):
    path: str  # "sales" or "sales/revenue"
    title: str
    description: str = ""


class Module(_Model):
    module_id: IdStr
    title: str
    description: str
    # operations = day-to-day work screens (orders, tickets, stock, ...); reports = the analytics report library
    section: Literal["operations", "reports"] = "reports"


class Application(_Model):
    schema_version: str = SCHEMA_VERSION
    app_id: IdStr
    name: str
    source_format: str = "native"  # "native" | "superset" | "appsmith" | ...
    # Relative dates ("last quarter", "this year") resolve against THIS date, not today's.
    # For a historical dataset (e.g. Olist, 2016-2018) set it to the last date in the data.
    as_of_date: str | None = None
    modules: list[Module]
    directories: list[Directory]
    pages: list[Page]
    widgets: list[Widget]
    datasets: list[Dataset]

    # ---- lookups used by the validator and retrieval
    def page(self, page_id: str) -> Page | None:
        return next((p for p in self.pages if p.page_id == page_id), None)

    def widget(self, widget_id: str) -> Widget | None:
        return next((w for w in self.widgets if w.widget_id == widget_id), None)

    def dataset(self, dataset_id: str) -> Dataset | None:
        return next((d for d in self.datasets if d.dataset_id == dataset_id), None)

    @model_validator(mode="after")
    def _referential_integrity(self) -> "Application":
        """Reject inconsistent exports at load time so the agent never sees them."""
        errs: list[str] = []
        page_ids = [p.page_id for p in self.pages]
        widget_ids = [w.widget_id for w in self.widgets]
        routes = [p.route for p in self.pages]
        for name, ids in (("page_id", page_ids), ("widget_id", widget_ids), ("route", routes)):
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                errs.append(f"duplicate {name}: {sorted(dup)}")
        dirs = {d.path for d in self.directories}
        ds = {d.dataset_id: d for d in self.datasets}
        wmap = {w.widget_id: w for w in self.widgets}
        for name, codes in (("page_code", [p.page_code for p in self.pages]),
                            ("widget_code", [w.widget_code for w in self.widgets])):
            codes = [c for c in codes if c]
            dup = {c for c in codes if codes.count(c) > 1}
            if dup:
                errs.append(f"duplicate {name}: {sorted(dup)}")
        for p in self.pages:
            if p.directory not in dirs:
                errs.append(f"{p.page_id}: unknown directory {p.directory}")
            for wid in p.widgets:
                if wid not in wmap:
                    errs.append(f"{p.page_id}: unknown widget {wid}")
            if p.layout is not None:
                flat = [wid for row in p.layout for wid in row]
                if sorted(flat) != sorted(p.widgets):
                    errs.append(f"{p.page_id}: layout must place every page widget exactly once")
            fids = {f.filter_id for f in p.filters}
            if len(fids) != len(p.filters):
                errs.append(f"{p.page_id}: duplicate filter_id")
            action_ids = [a.action_id for a in p.actions] + [
                a.action_id for wid in p.widgets if wid in wmap and wmap[wid].source for a in wmap[wid].source.row_actions]
            if len(action_ids) != len(set(action_ids)):
                errs.append(f"{p.page_id}: duplicate action_id")
            for wid in p.widgets:
                src = wmap[wid].source if wid in wmap else None
                if src:
                    for fid in src.filter_params:
                        if fid not in fids:
                            errs.append(f"{p.page_id}/{wid}: filter_params names unknown filter {fid}")
                    if src.date_params and "date_range" not in fids:
                        errs.append(f"{p.page_id}/{wid}: date_params but the page has no date_range filter")
                    cols = {c.name for c in src.columns}
                    if src.chart and not ({src.chart.x, *src.chart.y} <= cols):
                        errs.append(f"{p.page_id}/{wid}: chart uses columns that are not declared")
                    for a in src.row_actions:
                        missing = set(a.params_from_row.values()) - cols
                        if missing:
                            errs.append(f"{p.page_id}/{wid}: action {a.action_id} reads unknown columns {sorted(missing)}")
        for w in self.widgets:
            if w.dataset_id is None:
                continue  # API-bound widget, checked above
            if w.dataset_id not in ds:
                errs.append(f"{w.widget_id}: unknown dataset {w.dataset_id}")
                continue
            metric_ids = {m.metric_id for m in ds[w.dataset_id].metrics}
            for m in w.metrics:
                if m not in metric_ids:
                    errs.append(f"{w.widget_id}: unknown metric {m}")
            for dim in w.dimensions:
                if ds[w.dataset_id].field(dim) is None:
                    errs.append(f"{w.widget_id}: unknown dimension {dim}")
        if errs:
            raise ValueError("; ".join(errs))
        return self
