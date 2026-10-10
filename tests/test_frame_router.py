"""Unit tests for Frame extraction, Router, and Compiler (WP3 in AGENT_V2_DESIGN.md)."""
import pytest

from agent.frame import extract_frame_by_rules
from agent.router import route_frame
from agent.compiler import compile_frame_to_plan, merge_ui_steps
from contracts.actions import Intent, Plan, ToolCall


AS_OF = "2018-08-31"


def test_rules_frame_extraction():
    # 1. Unsafe refusal
    f_unsafe = extract_frame_by_rules("Delete all orders from last year", {}, as_of=AS_OF)
    assert f_unsafe is not None
    assert f_unsafe.act == "decline"
    assert f_unsafe.confidence == 1.0

    # 2. Audit query 1: AOV in SP, last quarter
    f1 = extract_frame_by_rules("Average order value in SP, last quarter", {}, as_of=AS_OF)
    assert f1 is not None
    assert f1.act == "open"
    assert f1.slices[0].metric == "aov"
    assert f1.slices[0].filters == {"customer_state": ["SP"]}
    assert f1.period is not None
    assert f1.period.preset == "last_quarter"

    # 3. Audit query 2: AOV in SP and RJ, last 3 months
    f2 = extract_frame_by_rules("AOV in SP and RJ, last 3 months", {}, as_of=AS_OF)
    assert f2 is not None
    assert f2.slices[0].filters == {"customer_state": ["RJ", "SP"]}
    assert f2.period is not None
    assert f2.period.preset == "months:3"


def test_routing_decisions():
    # 1. Refusal -> Converse
    f_refuse = extract_frame_by_rules("Delete all orders", {}, as_of=AS_OF)
    r_refuse = route_frame(f_refuse)
    assert r_refuse.skill == "Converse"

    # 2. Audit query 1 -> Operate on sales.aov
    f1 = extract_frame_by_rules("Average order value in SP, last quarter", {}, as_of=AS_OF)
    r1 = route_frame(f1)
    assert r1.skill == "Operate"
    assert r1.target_page_id == "sales.aov"

    # 3. Explain -> Analyze
    f_why = extract_frame_by_rules("Why did revenue go down from last month?", {}, as_of=AS_OF)
    r_why = route_frame(f_why)
    assert r_why.skill == "Analyze"


def test_compile_frame_to_atomic_plan():
    f1 = extract_frame_by_rules("Average order value in SP, last quarter", {}, as_of=AS_OF)
    r1 = route_frame(f1)
    plan = compile_frame_to_plan(f1, r1)

    assert plan.intent.name == "navigate"
    # First step MUST be atomic navigate
    assert plan.steps[0].tool == "navigate"
    args = plan.steps[0].args
    assert args["page_id"] == "sales.aov"
    assert args["filters"] == {"customer_state": ["SP"]}
    assert args["date_range"]["preset"] == "last_quarter"
    assert args["date_range"]["from"] == "2018-04-01"
    assert args["date_range"]["to"] == "2018-06-30"


def test_step_merging():
    """Sequential UI steps must merge into a SINGLE navigate step."""
    sequential_plan = Plan(
        intent=Intent(name="navigate"),
        steps=[
            ToolCall(tool="navigate", args={"page_id": "sales.aov"}),
            ToolCall(tool="set_filter", args={"filter_id": "customer_state", "value": "SP"}),
            ToolCall(tool="set_date_range", args={"preset": "last_quarter"}),
            ToolCall(tool="read_view", args={"widget_id": "sales.aov.kpi"}),
        ]
    )

    merged = merge_ui_steps(sequential_plan)
    # Total UI steps should now be exactly 1, plus 1 read_view
    assert len(merged.steps) == 2
    assert merged.steps[0].tool == "navigate"
    assert merged.steps[0].args["page_id"] == "sales.aov"
    assert merged.steps[0].args["filters"] == {"customer_state": "SP"}
    assert merged.steps[0].args["date_range"] == {"preset": "last_quarter"}
    assert merged.steps[1].tool == "read_view"
