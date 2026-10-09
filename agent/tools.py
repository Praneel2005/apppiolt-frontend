"""Tool schemas generated dynamically from Application metadata and API catalog.

Generates bounded, grounded JSON schemas for the 15 tools in contracts/actions.py,
using real enums for page IDs, filter IDs/values, metrics, dimensions, presets, and APIs.
"""
from __future__ import annotations

from typing import Any

from contracts.actions import ToolName
from contracts.metadata import Application, Page

DATE_PRESETS = [
    "last_7_days",
    "last_30_days",
    "last_90_days",
    "last_12_months",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "this_year",
    "year_to_date",
    "last_year",
]


def _normalize_catalog(catalog: dict[str, dict] | list[dict] | None) -> dict[str, dict]:
    if not catalog:
        return {}
    if isinstance(catalog, list):
        return {item["api_id"]: item for item in catalog if "api_id" in item}
    return catalog


def tool_schemas(
    app: Application,
    catalog: dict[str, dict] | list[dict] | None = None,
    current_page: Page | None = None,
) -> list[dict[str, Any]]:
    """Returns JSON schemas for each tool, grounded in application metadata."""
    cat = _normalize_catalog(catalog)

    # 1. Page candidates
    page_ids = [p.page_id for p in app.pages]
    routes = [p.route for p in app.pages]

    # 2. Filter candidates
    page_filters = (
        [f.filter_id for f in current_page.filters]
        if current_page
        else sorted({f.filter_id for p in app.pages for f in p.filters})
    )

    # 3. Metrics and dimensions from datasets
    all_metrics = sorted({m.metric_id for ds in app.datasets for m in ds.metrics})
    additive_metrics = sorted(
        {m.metric_id for ds in app.datasets for m in ds.metrics if getattr(m, "additive", True)}
    )
    if not additive_metrics:
        additive_metrics = all_metrics

    all_dimensions = sorted({f.name for ds in app.datasets for f in ds.fields if f.role == "dimension"})

    # 4. APIs
    read_apis = sorted([k for k, v in cat.items() if v.get("kind") == "read"]) or ["orders.search"]
    write_apis = sorted([k for k, v in cat.items() if v.get("kind") == "write"]) or ["tickets.create"]

    # 5. Widgets
    widget_ids = (
        current_page.widgets
        if current_page
        else [w.widget_id for w in app.widgets]
    )

    schemas = [
        {
            "name": "search_pages",
            "description": "Lexical and semantic search across application pages and operational modules.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search term or concept."}},
                "required": ["query"],
            },
        },
        {
            "name": "search_apis",
            "description": "Search catalogue endpoints for reading or modifying application entities.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Entity or action query."}},
                "required": ["query"],
            },
        },
        {
            "name": "navigate",
            "description": "Navigate to a specific application page, resetting state to page defaults.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "enum": page_ids, "description": "Target page ID."},
                    "keep_state": {"type": "boolean", "description": "Preserve compatible filters and dates."},
                },
                "required": ["page_id"],
            },
        },
        {
            "name": "set_filter",
            "description": "Apply a filter to the current page view.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_id": {"type": "string", "enum": page_filters, "description": "Filter identifier."},
                    "value": {
                        "description": "Value or array of values (e.g. 'SP', ['SP', 'RJ']).",
                        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "array", "items": {"type": "string"}}],
                    },
                },
                "required": ["filter_id", "value"],
            },
        },
        {
            "name": "clear_filter",
            "description": "Remove an active filter from the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_id": {"type": "string", "enum": page_filters, "description": "Filter to clear."},
                },
                "required": ["filter_id"],
            },
        },
        {
            "name": "set_date_range",
            "description": "Set active date range using a governed preset or explicit bounds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "preset": {"type": "string", "enum": DATE_PRESETS, "description": "Named relative preset."},
                    "from": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                    "to": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                },
            },
        },
        {
            "name": "set_sort",
            "description": "Sort the active table widget on the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "Column name to sort by."},
                    "direction": {"type": "string", "enum": ["asc", "desc"]},
                },
                "required": ["field", "direction"],
            },
        },
        {
            "name": "read_view",
            "description": "Read rows currently displayed on a widget on the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "widget_id": {"type": "string", "enum": widget_ids or None, "description": "Optional widget ID."},
                    "limit": {"type": "integer", "description": "Max rows to read (default 10)."},
                },
            },
        },
        {
            "name": "run_metric_query",
            "description": "Execute a governed metric aggregation across dimensions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": all_metrics, "description": "Governed metric name."},
                    "group_by": {
                        "description": "Dimension or dimensions to aggregate by.",
                        "anyOf": [{"type": "string", "enum": all_dimensions}, {"type": "array", "items": {"type": "string", "enum": all_dimensions}}],
                    },
                    "preset": {"type": "string", "enum": DATE_PRESETS},
                    "limit": {"type": "integer", "description": "Top N records."},
                },
                "required": ["metric"],
            },
        },
        {
            "name": "compare_periods",
            "description": "Compare metric between two periods, with contribution or mix/rate split.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": all_metrics, "description": "Governed metric name."},
                    "preset": {"type": "string", "description": "Target period (e.g. 'last_quarter', 'month:2017-11')."},
                    "group_by": {"type": "string", "enum": all_dimensions, "description": "Breakdown dimension."},
                    "limit": {"type": "integer"},
                },
                "required": ["metric"],
            },
        },
        {
            "name": "explain_change",
            "description": "Find top drivers contributing to an additive metric's change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": additive_metrics, "description": "Additive metric name."},
                    "preset": {"type": "string", "description": "Target period."},
                    "group_by": {"type": "string", "enum": all_dimensions, "description": "Driver dimension."},
                },
                "required": ["metric"],
            },
        },
        {
            "name": "analyze_trend",
            "description": "Compute trend line statistics (slope, peak, MoM, YoY, gaps) for a metric.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": all_metrics, "description": "Metric name."},
                    "group_by": {"type": "string", "enum": all_dimensions},
                },
                "required": ["metric"],
            },
        },
        {
            "name": "open_deep_link",
            "description": "Open an application deep link URL containing route, filters, and preset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "route": {"type": "string", "enum": routes, "description": "Destination route."},
                },
                "required": ["route"],
            },
        },
        {
            "name": "call_api",
            "description": "Call a read-only catalogue API to query business entities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "enum": read_apis, "description": "Catalogue API identifier."},
                    "params": {"type": "object", "description": "Query parameters for the endpoint."},
                },
                "required": ["api_id"],
            },
        },
        {
            "name": "write_api",
            "description": "Execute a state mutation API (creates preview, asks user confirmation, logs audit).",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "enum": write_apis, "description": "Catalogue write API ID."},
                    "params": {"type": "object", "description": "Path/query parameters."},
                    "body": {"type": "object", "description": "Payload body fields."},
                    "summary": {"type": "string", "description": "Short explanation for user confirmation."},
                },
                "required": ["api_id"],
            },
        },
        {
            "name": "render_canvas",
            "description": "Render a custom generated chart, data table, or action form on the canvas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["chart", "table", "form"]},
                    "evidence_id": {"type": "string", "description": "Evidence ID ('E1', '@last') for chart/table."},
                    "title": {"type": "string", "description": "Canvas card title."},
                    "chart_type": {"type": "string", "enum": ["bar_chart", "line_chart"]},
                    "x": {"type": "string", "description": "X-axis column name."},
                    "y": {"type": "array", "items": {"type": "string"}, "description": "Y-axis numeric column(s)."},
                    "api_id": {"type": "string", "enum": write_apis, "description": "API ID for form kind."},
                    "defaults": {"type": "object", "description": "Default field values for form."},
                },
                "required": ["kind"],
            },
        },
    ]

    return schemas
