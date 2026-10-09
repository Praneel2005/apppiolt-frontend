"""End-to-end tests for AppPilot master agent loop (agent/agent.py)."""
import asyncio
import pytest

from agent.agent import AppPilotAgent
from agent.llm import FakeLLM
from backend.routes.session import set_agent
from backend.sessions import create_session, get_session
from contracts.actions import AgentConfig, Intent, Plan, ToolCall
from starlette.testclient import TestClient
from backend.main import app


@pytest.mark.anyio
async def test_agent_loop_navigation():
    """Tests full navigation & state manipulation via agent loop."""
    s = create_session()
    fake = FakeLLM()

    # Pre-configure planned response
    fake.queue_response(
        Plan(
            intent=Intent(name="set_state"),
            steps=[
                ToolCall(tool="navigate", args={"page_id": "ops.orders"}),
                ToolCall(tool="set_filter", args={"filter_id": "customer_state", "value": "RJ"}),
            ],
            allow_replan=False,
        )
    )

    agent = AppPilotAgent(llm=fake)
    events = []
    async for ev in agent.handle(s.id, "open orders in RJ"):
        events.append(ev)

    types = [e.type for e in events]
    assert "understanding" in types
    assert "retrieval" in types
    assert "plan" in types
    assert "validation" in types
    assert "action" in types
    assert "verify" in types
    assert "answer" in types
    assert "done" in types

    # Verify session state updated correctly
    session = get_session(s.id)
    assert session.state.page_id == "ops.orders"
    assert "customer_state" in session.state.filters

    # Check answer event
    ans = next(e for e in events if e.type == "answer")
    assert "orders" in ans.data["text"].lower() or "rj" in ans.data["text"].lower()

    # Check done event
    done = next(e for e in events if e.type == "done")
    assert done.data["steps"] == 2


@pytest.mark.anyio
async def test_agent_loop_analytics_and_canvas():
    """Tests analytic query, evidence creation, and canvas rendering."""
    s = create_session()
    fake = FakeLLM()

    fake.queue_response(
        Plan(
            intent=Intent(name="analyze_trend"),
            steps=[
                ToolCall(
                    tool="run_metric_query",
                    args={
                        "metric": "revenue",
                        "group_by": ["customer_state"],
                        "preset": "last_quarter",
                        "limit": 5,
                    },
                ),
                ToolCall(
                    tool="render_canvas",
                    args={
                        "kind": "chart",
                        "evidence_id": "@last",
                        "title": "Revenue by State",
                        "x": "customer_state",
                        "y": ["value"],
                    },
                ),
            ],
            allow_replan=False,
        )
    )

    agent = AppPilotAgent(llm=fake)
    events = []
    async for ev in agent.handle(s.id, "revenue by state last quarter"):
        events.append(ev)

    types = [e.type for e in events]
    assert "evidence" in types
    assert "canvas" in types
    assert "answer" in types

    session = get_session(s.id)
    assert len(session.evidence) >= 1
    assert len(session.canvases) == 1

    ans = next(e for e in events if e.type == "answer")
    assert len(ans.data["citations"]) >= 1


@pytest.mark.anyio
async def test_agent_loop_guardrails_clarify_and_unsafe():
    """Tests clarification needed and unsafe action refusals."""
    s = create_session()
    fake = FakeLLM()

    # 1. Clarification needed
    fake.queue_response(
        Plan(
            intent=Intent(name="clarify_needed"),
            steps=[],
            clarifying_question="Would you like to view late orders or customer tickets?",
            allow_replan=False,
        )
    )

    agent = AppPilotAgent(llm=fake)
    events = []
    async for ev in agent.handle(s.id, "show me late stuff"):
        events.append(ev)

    ans = next(e for e in events if e.type == "answer")
    assert "late orders or customer tickets" in ans.data["text"]
    done = next(e for e in events if e.type == "done")
    assert done.data["steps"] == 0

    # 2. Unsafe action refusal
    fake.queue_response(
        Plan(
            intent=Intent(name="unsafe_action"),
            steps=[],
            clarifying_question="Database deletion operations are not permitted.",
            allow_replan=False,
        )
    )
    events_unsafe = []
    async for ev in agent.handle(s.id, "delete all orders from SP"):
        events_unsafe.append(ev)

    ans_unsafe = next(e for e in events_unsafe if e.type == "answer")
    assert "not permitted" in ans_unsafe.data["text"].lower()


import httpx


@pytest.mark.anyio
async def test_agent_wired_to_session_message_endpoint():
    """Tests session message endpoint when an agent is attached."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        s_res = (await client.post("/api/session")).json()
        sid = s_res["session_id"]

        fake = FakeLLM()
        fake.queue_response(
            Plan(
                intent=Intent(name="set_state"),
                steps=[ToolCall(tool="navigate", args={"page_id": "ops.orders"})],
                allow_replan=False,
            )
        )

        test_agent = AppPilotAgent(llm=fake)
        set_agent(test_agent)

        try:
            res = await client.post(f"/api/session/{sid}/message", json={"text": "go to orders"})
            assert res.status_code == 202
            assert res.json()["accepted"] is True

            deadline = asyncio.get_event_loop().time() + 5.0
            found_done = False
            while asyncio.get_event_loop().time() < deadline:
                evs = (await client.get(f"/api/session/{sid}/events")).json()["events"]
                if any(e["type"] == "done" for e in evs):
                    found_done = True
                    break
                await asyncio.sleep(0.1)

            assert found_done, "Agent handle did not finish emitting done event"

            # Verify state transitioned
            st = (await client.get(f"/api/session/{sid}/state")).json()["state"]
            assert st["page_id"] == "ops.orders"
        finally:
            set_agent(None)
