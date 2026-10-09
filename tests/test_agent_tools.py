"""Tests for agent/tools.py dynamic tool schema generation."""
from backend.config import app_model
from agent.tools import tool_schemas


def test_tool_schemas_generation():
    app = app_model()
    schemas = tool_schemas(app)

    names = {s["name"] for s in schemas}
    expected_tools = {
        "search_pages",
        "search_apis",
        "navigate",
        "set_filter",
        "clear_filter",
        "set_date_range",
        "set_sort",
        "read_view",
        "run_metric_query",
        "compare_periods",
        "explain_change",
        "analyze_trend",
        "open_deep_link",
        "call_api",
        "write_api",
        "render_canvas",
    }
    assert expected_tools <= names

    # Check navigate page_id enums
    nav_schema = next(s for s in schemas if s["name"] == "navigate")
    page_enum = nav_schema["parameters"]["properties"]["page_id"]["enum"]
    assert "ops.dashboard" in page_enum
    assert "ops.orders" in page_enum

    # Check metric enums
    metric_schema = next(s for s in schemas if s["name"] == "run_metric_query")
    metric_enum = metric_schema["parameters"]["properties"]["metric"]["enum"]
    assert "revenue" in metric_enum


def test_tool_schemas_with_current_page():
    app = app_model()
    orders_page = app.page("ops.orders")
    schemas = tool_schemas(app, current_page=orders_page)

    filter_schema = next(s for s in schemas if s["name"] == "set_filter")
    filter_enum = filter_schema["parameters"]["properties"]["filter_id"]["enum"]
    page_filter_ids = [f.filter_id for f in orders_page.filters]
    assert set(filter_enum) == set(page_filter_ids)


def test_plan_from_schemas_validates():
    from backend.validator import validate
    from contracts.actions import Plan, ToolCall, Intent

    app = app_model()
    p = Plan(
        intent=Intent(name="set_state"),
        steps=[
            ToolCall(tool="navigate", args={"page_id": "ops.orders"}),
            ToolCall(tool="set_filter", args={"filter_id": "customer_state", "value": "SP"}),
            ToolCall(tool="set_date_range", args={"preset": "last_month"}),
        ]
    )
    res = validate(app, p, None, {})
    assert res.ok is True
    assert res.issues == []
