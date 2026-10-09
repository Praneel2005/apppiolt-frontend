"""Tests for agent/baselines.py (B0, B1, B2)."""
import pytest

from agent.baselines import BaselineB0Agent, BaselineB1Agent, BaselineB2Agent
from agent.llm import FakeLLM
from backend.sessions import create_session
from contracts.actions import Intent, Plan, ToolCall


@pytest.mark.anyio
async def test_baseline_b0_llm_only():
    s = create_session()
    fake = FakeLLM()
    fake.queue_response(
        Plan(
            intent=Intent(name="set_state"),
            steps=[ToolCall(tool="navigate", args={"page_id": "ops.orders"})],
            allow_replan=False,
        )
    )

    agent = BaselineB0Agent(llm=fake)
    events = []
    async for ev in agent.handle(s.id, "open orders"):
        events.append(ev)

    types = [e.type for e in events]
    assert "understanding" in types
    assert "plan" in types
    assert "done" in types
    # B0 should have empty retrieval
    ret_ev = next(e for e in events if e.type == "retrieval")
    assert len(ret_ev.data["pages"]) == 0


@pytest.mark.anyio
async def test_baseline_b1_prompt_stuffing():
    s = create_session()
    fake = FakeLLM()
    fake.queue_response(
        Plan(
            intent=Intent(name="set_state"),
            steps=[ToolCall(tool="navigate", args={"page_id": "ops.orders"})],
            allow_replan=False,
        )
    )

    agent = BaselineB1Agent(llm=fake)
    events = []
    async for ev in agent.handle(s.id, "open orders"):
        events.append(ev)

    types = [e.type for e in events]
    assert "understanding" in types
    assert "retrieval" in types
    assert "plan" in types
    assert "done" in types
    # B1 retrieval should be populated with all pages
    ret_ev = next(e for e in events if e.type == "retrieval")
    assert len(ret_ev.data["pages"]) > 50 or len(ret_ev.data["pages"]) == 10  # Top 10 logged


@pytest.mark.anyio
async def test_baseline_b2_rag_no_gates():
    s = create_session()
    fake = FakeLLM()
    fake.queue_response(
        Plan(
            intent=Intent(name="set_state"),
            steps=[ToolCall(tool="navigate", args={"page_id": "ops.orders"})],
            allow_replan=False,
        )
    )

    agent = BaselineB2Agent(llm=fake)
    events = []
    async for ev in agent.handle(s.id, "open orders"):
        events.append(ev)

    types = [e.type for e in events]
    assert "understanding" in types
    assert "retrieval" in types
    assert "plan" in types
    assert "done" in types
    # B2 does NOT emit validation event because validator is disabled
    assert "validation" not in types
