"""Tests for agent/planner.py single-call planning and repair loop."""
import asyncio
from agent.context import SessionMemory
from agent.llm import FakeLLM
from agent.planner import plan_request
from contracts.actions import AgentConfig, Intent, Plan, ToolCall


def test_planner_basic_flow():
    fake = FakeLLM()
    expected_plan = Plan(
        intent=Intent(name="set_state"),
        steps=[
            ToolCall(tool="navigate", args={"page_id": "ops.orders"}),
            ToolCall(tool="set_filter", args={"filter_id": "customer_state", "value": "RJ"}),
        ],
    )
    fake.queue_response(expected_plan)

    mem = SessionMemory(session_id="s1")
    page_ctx = {"page": {"page_id": "ops.dashboard"}}

    async def _run():
        plan, ret = await plan_request("orders in RJ", page_ctx, mem, llm=fake)
        assert plan.intent.name == "set_state"
        assert len(plan.steps) == 2
        assert plan.steps[0].tool == "navigate"
        assert len(ret.pages) > 0

    asyncio.run(_run())


def test_planner_resolves_deictic_reference():
    fake = FakeLLM()
    # Step has placeholder @resolved
    expected_plan = Plan(
        intent=Intent(name="explain_change"),
        steps=[
            ToolCall(tool="call_api", args={"api_id": "orders.get", "params": {"order_ref": "@resolved"}}),
        ],
    )
    fake.queue_response(expected_plan)

    mem = SessionMemory(session_id="s2")
    page_ctx = {
        "page": {"page_id": "ops.orders"},
        "widgets": [{"rows": [{"order_id": "ORD-TARGET-99"}]}],
    }

    async def _run():
        plan, _ = await plan_request("why was this order late?", page_ctx, mem, llm=fake)
        assert plan.steps[0].args["params"]["order_ref"] == "ORD-TARGET-99"

    asyncio.run(_run())


def test_planner_validation_repair_loop():
    fake = FakeLLM()
    # 1st response: invalid filter value for state
    invalid_plan = Plan(
        intent=Intent(name="set_state"),
        steps=[
            ToolCall(tool="navigate", args={"page_id": "ops.orders"}),
            ToolCall(tool="set_filter", args={"filter_id": "customer_state", "value": "INVALID_STATE_XYZ"}),
        ],
    )
    # 2nd response: corrected filter value
    valid_plan = Plan(
        intent=Intent(name="set_state"),
        steps=[
            ToolCall(tool="navigate", args={"page_id": "ops.orders"}),
            ToolCall(tool="set_filter", args={"filter_id": "customer_state", "value": "SP"}),
        ],
    )
    fake.queue_response(invalid_plan)
    fake.queue_response(valid_plan)

    mem = SessionMemory(session_id="s3")
    page_ctx = {"page": {"page_id": "ops.orders"}}

    async def _run():
        cfg = AgentConfig(use_validator=True, max_plan_retries=2)
        plan, _ = await plan_request("filter to SP", page_ctx, mem, llm=fake, config=cfg)
        assert plan.steps[1].args["value"] == "SP"
        assert len(fake.call_logs) == 2  # Proves that repair loop triggered 2nd call

    asyncio.run(_run())
