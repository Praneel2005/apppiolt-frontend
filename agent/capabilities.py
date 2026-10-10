"""Capability model for AppPilot (WP1 in AGENT_V2_DESIGN.md).

Constructed once from application metadata and the API catalogue.
Provides an immutable in-memory capability graph:
- What each page can filter, group by, sort, and display
- Which datasets provide which metrics and dimensions
- Which APIs read or mutate which entities
- Geographic entity hierarchies (State ⊂ Region) and synonyms
- Capability query helpers: pages_supporting, best_page, missing
- Compact capability slice generator for planner prompt (fixes C1, C2)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from backend.config import app_model
from contracts.metadata import Application, Page

# Brazilian geographic hierarchy
BRAZIL_REGIONS: dict[str, list[str]] = {
    "Southeast": ["SP", "RJ", "MG", "ES"],
    "South": ["PR", "SC", "RS"],
    "Northeast": ["BA", "PE", "CE", "MA", "PB", "RN", "AL", "PI", "SE"],
    "Central-West": ["GO", "MT", "MS", "DF"],
    "North": ["AM", "PA", "RO", "TO", "AC", "AP", "RR"],
}

STATE_TO_REGION: dict[str, str] = {
    state: region for region, states in BRAZIL_REGIONS.items() for state in states
}

STATE_SYNONYMS: dict[str, str] = {
    "sao paulo": "SP",
    "são paulo": "SP",
    "sp": "SP",
    "rio de janeiro": "RJ",
    "rio": "RJ",
    "rj": "RJ",
    "minas gerais": "MG",
    "minas": "MG",
    "mg": "MG",
    "espirito santo": "ES",
    "espírito santo": "ES",
    "es": "ES",
    "parana": "PR",
    "paraná": "PR",
    "pr": "PR",
    "santa catarina": "SC",
    "sc": "SC",
    "rio grande do sul": "RS",
    "rs": "RS",
    "bahia": "BA",
    "ba": "BA",
    "pernambuco": "PE",
    "pe": "PE",
    "ceara": "CE",
    "ceará": "CE",
    "ce": "CE",
    "distrito federal": "DF",
    "brasilia": "DF",
    "brasília": "DF",
    "df": "DF",
}


@dataclass(frozen=True)
class FilterCapability:
    filter_id: str
    field: str
    type: str  # date_range, multiselect, select, text, number_range
    allowed_values: tuple[str, ...] = ()
    allowed_ops: tuple[str, ...] = ()
    synonyms: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class PageCapability:
    page_id: str
    page_code: str | None
    route: str
    title: str
    description: str
    agent_context: str
    kind: str  # report | operations
    directory: str
    supports_date_range: bool
    filters: dict[str, FilterCapability] = field(default_factory=dict)
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    sortable_fields: tuple[str, ...] = ()
    row_widgets: tuple[str, ...] = ()
    actions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class DatasetCapability:
    dataset_id: str
    table: str
    description: str
    metrics: tuple[str, ...] = ()
    additive_metrics: tuple[str, ...] = ()
    ratio_metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    time_field: str | None = None


@dataclass(frozen=True)
class ApiCapability:
    api_id: str
    kind: str  # read | write
    title: str
    description: str
    entity: str
    path: str
    method: str
    parameters: tuple[dict[str, Any], ...] = ()
    body_schema: dict[str, Any] | None = None
    data_layers: tuple[str, ...] = ()
    requires_confirmation: bool = False
    undoable: bool = False


class CapabilityModel:
    """Immutable capability graph over application metadata and API catalogue."""

    def __init__(
        self,
        pages: dict[str, PageCapability],
        datasets: dict[str, DatasetCapability],
        apis: dict[str, ApiCapability],
    ):
        self.pages = pages
        self.datasets = datasets
        self.apis = apis

    def get_page(self, page_id: str) -> PageCapability | None:
        return self.pages.get(page_id)

    def pages_supporting(
        self,
        filters: list[str] | set[str] | None = None,
        group_by: list[str] | set[str] | None = None,
        metric: str | None = None,
        supports_date: bool = False,
    ) -> list[PageCapability]:
        """Finds pages that can satisfy all requested filters, group_bys, and metric."""
        flt_set = set(filters or [])
        grp_set = set(group_by or [])
        matches = []

        for p in self.pages.values():
            if flt_set and not flt_set.issubset(set(p.filters.keys())):
                continue
            if grp_set and not grp_set.issubset(set(p.dimensions)):
                continue
            if metric and metric not in p.metrics:
                continue
            if supports_date and not p.supports_date_range:
                continue
            matches.append(p)

        if metric:
            def _score_page(p: PageCapability) -> tuple[int, int, int]:
                id_match = 0 if (p.page_id.endswith(f".{metric}") or p.page_id == metric) else 1
                overview_penalty = 1 if "overview" in p.page_id else 0
                return (id_match, overview_penalty, len(p.metrics))

            matches.sort(key=_score_page)

        return matches

    def best_page(
        self,
        candidate_page_ids: list[str],
        filters: list[str] | set[str] | None = None,
        metric: str | None = None,
        current_page_id: str | None = None,
    ) -> PageCapability | None:
        """Picks the highest coverage page for the given filters and metric."""
        flt_set = set(filters or [])

        # 1. Prefer current page if it satisfies all filters and metric
        if current_page_id and current_page_id in self.pages:
            curr = self.pages[current_page_id]
            if flt_set.issubset(set(curr.filters.keys())):
                if metric is None or metric in curr.metrics:
                    return curr

        # 2. Check candidate pages in rank order
        best = None
        best_overlap = -1

        for pid in candidate_page_ids:
            p = self.pages.get(pid)
            if not p:
                continue
            overlap = len(flt_set.intersection(set(p.filters.keys())))
            if flt_set and flt_set.issubset(set(p.filters.keys())):
                if metric is None or metric in p.metrics:
                    return p
            if overlap > best_overlap:
                best_overlap = overlap
                best = p

        return best

    def missing_capabilities(
        self, page_id: str, requested_filters: list[str] | None = None
    ) -> list[str]:
        """Returns filter IDs that this page does NOT support."""
        p = self.pages.get(page_id)
        if not p or not requested_filters:
            return []
        supported = set(p.filters.keys())
        return [f for f in requested_filters if f not in supported]

    def compact_slice(
        self, page_ids: list[str], api_ids: list[str]
    ) -> str:
        """Builds a compact, highly informative grounded capability slice for the planner prompt."""
        lines = ["GROUNDED CAPABILITY SLICE (Available Target Pages & Valid Filters):"]

        for pid in page_ids[:6]:
            p = self.pages.get(pid)
            if not p:
                continue
            flt_descs = []
            for fid, f in p.filters.items():
                if f.allowed_values and len(f.allowed_values) <= 8:
                    flt_descs.append(f"{fid} (allowed: {list(f.allowed_values)})")
                elif f.allowed_values:
                    flt_descs.append(f"{fid} ({len(f.allowed_values)} values, e.g. {f.allowed_values[:3]})")
                else:
                    flt_descs.append(f"{fid} ({f.type})")

            date_str = "yes" if p.supports_date_range else "no"
            dims_str = f", dims: {list(p.dimensions[:4])}" if p.dimensions else ""
            lines.append(
                f"  - Page '{p.title}' (page_id: '{p.page_id}', route: '{p.route}', date_range: {date_str}{dims_str})\n"
                f"    Filters: {flt_descs if flt_descs else 'none'}"
            )

        if api_ids:
            lines.append("\nGROUNDED APIS:")
            for aid in api_ids[:4]:
                a = self.apis.get(aid)
                if not a:
                    continue
                req_params = [
                    p["name"] for p in a.parameters if p.get("required")
                ]
                lines.append(
                    f"  - API '{a.title}' (api_id: '{a.api_id}', kind: '{a.kind}', path: '{a.path}', req_params: {req_params})"
                )

        return "\n".join(lines)


@lru_cache(maxsize=1)
def build_capability_model() -> CapabilityModel:
    """Builds the capability model once from application metadata and API catalogue."""
    from backend.routes.system import catalog_entries
    from backend.main import app as asgi_app

    app: Application = app_model()

    # 1. Dataset capabilities
    dataset_caps = {}
    for ds in app.datasets:
        metrics = tuple(m.metric_id for m in ds.metrics)
        additive = tuple(m.metric_id for m in ds.metrics if getattr(m, "additive", True))
        ratio = tuple(m.metric_id for m in ds.metrics if not getattr(m, "additive", True))
        dims = tuple(f.name for f in ds.fields if f.role == "dimension")
        time_field = ds.time_field
        dataset_caps[ds.dataset_id] = DatasetCapability(
            dataset_id=ds.dataset_id,
            table=ds.table,
            description=ds.description,
            metrics=metrics,
            additive_metrics=additive,
            ratio_metrics=ratio,
            dimensions=dims,
            time_field=time_field,
        )

    # 2. Page capabilities
    page_caps = {}
    for p in app.pages:
        filters_map = {}
        supports_date = any(f.type == "date_range" for f in p.filters) or bool(
            p.default_state.get("date_range")
        )

        for f in p.filters:
            filters_map[f.filter_id] = FilterCapability(
                filter_id=f.filter_id,
                field=f.field,
                type=f.type,
                allowed_values=tuple(f.allowed_values or ()),
                allowed_ops=tuple(f.allowed_ops or ()),
                synonyms=f.synonyms or {},
            )

        # Collect metrics and dimensions from widgets on this page
        p_metrics: set[str] = set()
        p_dims: set[str] = set()
        sort_fields: set[str] = set()
        row_widgets: list[str] = []

        for wid in p.widgets:
            w = app.widget(wid)
            p_metrics.update(w.metrics)
            p_dims.update(w.dimensions)
            if "sort" in w.supports:
                sort_fields.update(w.dimensions)
                sort_fields.update(w.metrics)
            if w.type in ("grid", "filter_bar"):
                row_widgets.append(w.widget_id)

        actions_list = tuple(
            {"action_id": a.action_id, "api_id": a.api_id, "label": a.label}
            for a in p.actions
        )

        page_caps[p.page_id] = PageCapability(
            page_id=p.page_id,
            page_code=p.page_code,
            route=p.route,
            title=p.title,
            description=p.description,
            agent_context=p.agent_context,
            kind=p.kind,
            directory=p.directory,
            supports_date_range=supports_date,
            filters=filters_map,
            metrics=tuple(sorted(p_metrics)),
            dimensions=tuple(sorted(p_dims)),
            sortable_fields=tuple(sorted(sort_fields)),
            row_widgets=tuple(row_widgets),
            actions=actions_list,
        )

    # 3. API capabilities
    api_caps = {}
    try:
        entries = catalog_entries(asgi_app)
        for e in entries:
            aid = e["api_id"]
            api_caps[aid] = ApiCapability(
                api_id=aid,
                kind=e.get("kind", "read"),
                title=e.get("title", aid),
                description=e.get("description", ""),
                entity=e.get("entity", ""),
                path=e.get("path", ""),
                method=e.get("method", "GET"),
                parameters=tuple(e.get("parameters", ())),
                body_schema=e.get("body_schema"),
                data_layers=tuple(e.get("data_layers", ())),
                requires_confirmation=e.get("requires_confirmation", False),
                undoable=e.get("undoable", False),
            )
    except Exception:
        pass

    return CapabilityModel(
        pages=page_caps,
        datasets=dataset_caps,
        apis=api_caps,
    )


def get_capability_model() -> CapabilityModel:
    return build_capability_model()
