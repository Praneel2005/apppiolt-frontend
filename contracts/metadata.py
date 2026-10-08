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

SCHEMA_VERSION = "0.1.0"

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
    # explain_change only does contribution decomposition on additive metrics.
    additive: bool = True


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


class Widget(_Model):
    widget_id: IdStr
    type: Literal["grid", "bar_chart", "line_chart", "pie_chart", "kpi_card", "filter_bar"]
    title: str
    description: str  # written for retrieval: say what question this widget answers
    dataset_id: IdStr
    metrics: list[IdStr] = []
    dimensions: list[str] = []
    supports: list[Literal["sort", "filter", "date_range", "drilldown"]] = []
    render: RenderContract | None = None


# --------------------------------------------------------------- pages/routes
class Page(_Model):
    page_id: IdStr
    title: str
    description: str  # written for retrieval
    directory: str  # navigation hierarchy path, e.g. "sales/revenue"
    route: str  # unique, e.g. "/sales/revenue-by-region"
    keywords: list[str] = []
    widgets: list[IdStr]
    filters: list[FilterDef] = []
    default_state: dict = {}

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
        for p in self.pages:
            if p.directory not in dirs:
                errs.append(f"{p.page_id}: unknown directory {p.directory}")
            for wid in p.widgets:
                if wid not in wmap:
                    errs.append(f"{p.page_id}: unknown widget {wid}")
        for w in self.widgets:
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
