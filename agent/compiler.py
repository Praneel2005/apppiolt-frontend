"""Compiler: transforms Frames or sequential UI steps into atomic executable Plans (WP3 in AGENT_V2_DESIGN.md).

Key Responsibilities:
- Step Merging: merges navigate + set_filter + set_date_range into ONE atomic navigate step.
- Single apply_state: ensures exactly one WebSocket state push and one render_ack per turn.
- Frame Compilation: compiles Frame + RoutingDecision into a fully validated Plan.
"""
from __future__ import annotations

from typing import Any, Optional

from agent.capabilities import get_capability_model
from agent.frame import Frame
from agent.router import RoutingDecision
from contracts.actions import Intent, Plan, ToolCall


def compile_frame_to_plan(
    frame: Frame,
    routing: RoutingDecision,
    current_page_id: Optional[str] = None,
) -> Plan:
    """Compiles a Frame and its RoutingDecision into an atomic Plan."""
    # 1. Converse
    if routing.skill == "Converse":
        intent_name = "unsafe_action" if frame.act == "decline" else "clarify_needed"
        question = (
            frame.ambiguity.question
            if frame.ambiguity
            else "How can I help you navigate AppPilot?"
        )
        return Plan(
            intent=Intent(name=intent_name, ambiguity=question),
            steps=[],
            clarifying_question=question,
            allow_replan=False,
        )

    # 2. Operate (Atomic View navigation)
    if routing.skill == "Operate":
        target_pid = routing.target_page_id or current_page_id or "sales.overview"
        primary_slice = frame.slices[0] if frame.slices else None

        # Build atomic navigate args
        nav_args: dict[str, Any] = {"page_id": target_pid}
        if routing.is_in_place_refinement:
            nav_args["keep_state"] = True

        if primary_slice and primary_slice.filters:
            nav_args["filters"] = primary_slice.filters

        if frame.period:
            nav_args["date_range"] = {
                "preset": frame.period.preset,
                "from": frame.period.from_date,
                "to": frame.period.to_date,
            }

        if frame.sort_limit and frame.sort_limit.field:
            nav_args["sort"] = {
                "field": frame.sort_limit.field,
                "dir": frame.sort_limit.dir,
            }

        steps = [
            ToolCall(
                tool="navigate",
                args=nav_args,
                expect={"route": f"/{target_pid.replace('.', '/')}"},
            )
        ]

        # Optional read step for the primary widget on this page
        cm = get_capability_model()
        page_cap = cm.get_page(target_pid)
        if page_cap and page_cap.row_widgets:
            steps.append(
                ToolCall(
                    tool="read_view",
                    args={"widget_id": page_cap.row_widgets[0], "max_rows": 20},
                )
            )

        return Plan(
            intent=Intent(name="navigate", slots=nav_args),
            steps=steps,
            allow_replan=False,
        )

    # 3. Analyze
    if routing.skill == "Analyze":
        primary_slice = frame.slices[0] if frame.slices else None
        metric = (primary_slice.metric if primary_slice else None) or frame.target.ref or "sales.revenue"

        # Determine analysis tool
        if frame.act == "trend" or "order_month" in frame.group_by:
            tool_name = "analyze_trend"
            args = {
                "metric": metric,
                "filters": primary_slice.filters if primary_slice else {},
                "date_from": frame.period.from_date if frame.period else None,
                "date_to": frame.period.to_date if frame.period else None,
            }
        elif frame.act == "explain":
            tool_name = "explain_change"
            args = {
                "metric": metric,
                "filters": primary_slice.filters if primary_slice else {},
                "date_from": frame.period.from_date if frame.period else None,
                "date_to": frame.period.to_date if frame.period else None,
            }
        elif frame.act == "compare":
            tool_name = "compare_periods"
            args = {
                "metric": metric,
                "filters": primary_slice.filters if primary_slice else {},
                "date_from": frame.period.from_date if frame.period else None,
                "date_to": frame.period.to_date if frame.period else None,
            }
        else:
            tool_name = "run_metric_query"
            args = {
                "metric": metric,
                "group_by": frame.group_by if frame.group_by else [],
                "filters": primary_slice.filters if primary_slice else {},
                "date_from": frame.period.from_date if frame.period else None,
                "date_to": frame.period.to_date if frame.period else None,
            }

        return Plan(
            intent=Intent(name="read_view", slots={"metric": metric}),
            steps=[ToolCall(tool=tool_name, args=args)],  # type: ignore
            allow_replan=False,
        )

    # Fallback default plan
    return Plan(
        intent=Intent(name="navigate"),
        steps=[ToolCall(tool="navigate", args={"page_id": "sales.overview"})],
        allow_replan=False,
    )


def merge_ui_steps(plan: Plan) -> Plan:
    """Merges consecutive UI state steps (navigate, set_filter, set_date_range, set_sort)

    into a SINGLE atomic navigate tool call. Leaves read, api, and analysis steps intact.
    """
    if not plan.steps:
        return plan

    has_ui = any(st.tool in ("navigate", "set_filter", "set_date_range", "set_sort") for st in plan.steps)
    if not has_ui:
        return plan

    # Check if there are multiple UI steps
    ui_steps = [st for st in plan.steps if st.tool in ("navigate", "set_filter", "set_date_range", "set_sort")]
    other_steps = [st for st in plan.steps if st.tool not in ("navigate", "set_filter", "set_date_range", "set_sort")]

    if len(ui_steps) <= 1 and all(k not in ("set_filter", "set_date_range", "set_sort") for k in [st.tool for st in ui_steps]):
        return plan

    # Consolidate into a single navigate call
    nav_args: dict[str, Any] = {"filters": {}}
    target_pid = None

    for st in ui_steps:
        if st.tool == "navigate":
            target_pid = st.args.get("page_id")
            if st.args.get("filters"):
                nav_args["filters"].update(st.args["filters"])
            if st.args.get("date_range"):
                nav_args["date_range"] = st.args["date_range"]
            if st.args.get("sort"):
                nav_args["sort"] = st.args["sort"]
            if st.args.get("keep_state"):
                nav_args["keep_state"] = True
        elif st.tool == "set_filter":
            fid = st.args.get("filter_id")
            val = st.args.get("value")
            if fid and val:
                nav_args["filters"][fid] = val
        elif st.tool == "set_date_range":
            nav_args["date_range"] = st.args
        elif st.tool == "set_sort":
            nav_args["sort"] = {"field": st.args.get("field"), "dir": st.args.get("dir", "desc")}

    if not target_pid:
        target_pid = "sales.overview"

    nav_args["page_id"] = target_pid
    if not nav_args["filters"]:
        nav_args.pop("filters")

    merged_step = ToolCall(tool="navigate", args=nav_args)
    return Plan(
        intent=plan.intent,
        steps=[merged_step] + other_steps,
        clarifying_question=plan.clarifying_question,
        allow_replan=plan.allow_replan,
    )
