import asyncio
import json
import pytest
from pydantic import BaseModel

from agent.llm import (
    DiskCache,
    FakeLLM,
    GeminiProvider,
    PlanFlat,
    TokenBucket,
    get_llm,
    plan_flat_to_plan,
    plan_to_plan_flat,
)
from contracts.actions import Intent, Plan, ToolCall


class DummySchema(BaseModel):
    message: str
    code: int = 200


def test_plan_flat_round_trip():
    original = Plan(
        intent=Intent(name="set_state", slots={"metric": "revenue"}),
        steps=[
            ToolCall(tool="navigate", args={"page_id": "ops.orders"}, expect={"route": "/ops/orders"}),
            ToolCall(tool="set_filter", args={"filter_id": "customer_state", "value": ["SP"]}),
        ],
        clarifying_question=None,
        allow_replan=True,
    )
    flat = plan_to_plan_flat(original)
    assert flat.intent.name == "set_state"
    assert json.loads(flat.intent.slots_json)["metric"] == "revenue"
    assert len(flat.steps) == 2
    assert flat.steps[0].tool == "navigate"
    assert json.loads(flat.steps[0].args_json)["page_id"] == "ops.orders"

    restored = plan_flat_to_plan(flat)
    assert restored.intent.name == original.intent.name
    assert restored.intent.slots == original.intent.slots
    assert len(restored.steps) == 2
    assert restored.steps[0].tool == original.steps[0].tool
    assert restored.steps[0].args == original.steps[0].args
    assert restored.steps[0].expect == original.steps[0].expect
    assert restored.allow_replan is True


def test_disk_cache(tmp_path):
    cache = DiskCache(tmp_path)
    model = "gemini-test"
    schema = "DummySchema"
    messages = [{"role": "user", "content": "ping"}]
    sys = "You are a test assistant."

    # Cache miss
    assert cache.get(model, schema, messages, sys) is None

    # Cache set
    resp = '{"message": "pong", "code": 200}'
    cache.set(model, schema, messages, sys, resp)

    # Cache hit
    hit = cache.get(model, schema, messages, sys)
    assert hit == resp


def test_token_bucket():
    async def _run():
        bucket = TokenBucket(requests_per_minute=60.0)  # 1 per second
        assert bucket.tokens == 60.0
        await bucket.acquire()
        assert bucket.tokens < 60.0
    asyncio.run(_run())


def test_fake_llm_queue_and_handlers():
    async def _run():
        fake = FakeLLM()

        # Default dummy for Plan
        plan = await fake.generate_structured(Plan, [{"role": "user", "content": "hello"}])
        assert plan.intent.name == "navigate"
        assert len(plan.steps) == 1

        # Queued response
        custom = DummySchema(message="custom hello", code=201)
        fake.queue_response(custom)
        res = await fake.generate_structured(DummySchema, [{"role": "user", "content": "hi"}])
        assert res.message == "custom hello"
        assert res.code == 201

        # Registered handler
        fake.register_handler(
            lambda msgs: "tickets" in msgs[0]["content"],
            {"message": "ticket response", "code": 100},
        )
        res_handled = await fake.generate_structured(DummySchema, [{"role": "user", "content": "search tickets"}])
        assert res_handled.message == "ticket response"
        assert res_handled.code == 100

        assert len(fake.call_logs) >= 1

    asyncio.run(_run())


def test_get_llm_factory():
    llm = get_llm("fake")
    assert isinstance(llm, FakeLLM)

    llm_gemini = get_llm("gemini", model_name="gemini-custom")
    assert isinstance(llm_gemini, GeminiProvider)
    assert llm_gemini.model_name == "gemini-custom"


def test_chained_llm_provider_failover():
    from agent.llm import ChainedLLMProvider

    class FailingProvider(FakeLLM):
        async def generate_structured(self, schema, messages, system_prompt=""):
            raise RuntimeError("Primary provider rate-limited or unavailable")

    primary = FailingProvider("failing-primary")
    fallback = FakeLLM("working-fallback")
    fallback.queue_response(DummySchema(message="fallback success", code=200))

    chained = ChainedLLMProvider(primary, fallback)

    async def _run():
        res = await chained.generate_structured(DummySchema, [{"role": "user", "content": "test"}])
        assert res.message == "fallback success"
        assert res.code == 200

    asyncio.run(_run())
