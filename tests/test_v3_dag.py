"""The task-plan scheduler: validation, dependency order, real parallelism, retries, timeouts, failure containment."""
import asyncio
import time

import pytest

from agent.dag import PlanError, Task, run_plan, validate_plan


async def collect(tasks, **kw):
    steps, result = [], None
    async for kind, payload in run_plan(tasks, **kw):
        if kind == "step":
            steps.append(payload)
        else:
            result = payload
    return steps, result


def t(id_, fn=None, deps=(), **kw):
    async def default(inputs):
        return id_

    return Task(id_, id_.upper(), fn or default, tuple(deps), **kw)


# ------------------------------------------------------------------------------------ validation
def test_levels_group_tasks_that_can_run_together():
    levels = validate_plan([t("a"), t("b"), t("c", deps=("a", "b")), t("d", deps=("c",))])
    assert levels == [["a", "b"], ["c"], ["d"]]


def test_invalid_plans_are_rejected_before_anything_runs():
    with pytest.raises(PlanError, match="duplicate"):
        validate_plan([t("a"), t("a")])
    with pytest.raises(PlanError, match="unknown"):
        validate_plan([t("a", deps=("ghost",))])
    with pytest.raises(PlanError, match="itself"):
        validate_plan([t("a", deps=("a",))])
    with pytest.raises(PlanError, match="cycle"):
        validate_plan([t("a", deps=("b",)), t("b", deps=("a",))])
    with pytest.raises(PlanError, match="not allowed"):
        validate_plan([t("a", kind="shell")], allowed_kinds={"scan", "trend"})
    validate_plan([t("a", kind="scan")], allowed_kinds={"scan"})


# ------------------------------------------------------------------------------------ running
@pytest.mark.anyio
async def test_dependants_receive_the_results_of_their_dependencies():
    async def add(inputs):
        return inputs["a"] + inputs["b"]

    async def a(_): return 2
    async def b(_): return 5
    steps, res = await collect([t("a", a), t("b", b), t("sum", add, deps=("a", "b"))])
    assert res.value("sum") == 7 and set(res.runs) == {"a", "b", "sum"}
    assert res.order.index("sum") > max(res.order.index("a"), res.order.index("b"))


@pytest.mark.anyio
async def test_independent_tasks_really_run_at_the_same_time():
    async def slow(_):
        await asyncio.sleep(0.2)
        return 1
    t0 = time.monotonic()
    _, res = await collect([t(f"s{i}", slow) for i in range(4)], max_parallel=4)
    assert time.monotonic() - t0 < 0.6 and len(res.runs) == 4      # four 0.2 s tasks take about 0.2 s, not 0.8 s


@pytest.mark.anyio
async def test_the_parallelism_limit_is_respected():
    live, peak = 0, 0

    async def work(_):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.05)
        live -= 1

    await collect([t(f"w{i}", work) for i in range(6)], max_parallel=2)
    assert peak == 2


@pytest.mark.anyio
async def test_a_failed_optional_task_gives_none_and_the_rest_continue():
    async def boom(_): raise RuntimeError("database timeout")
    async def use(inputs): return "got none" if inputs["x"] is None else "got value"
    steps, res = await collect([t("x", boom), t("y", use, deps=("x",)), t("z")])
    assert res.runs["x"].status == "failed" and "RuntimeError" in res.runs["x"].error
    assert res.value("y") == "got none" and res.value("z") == "z" and res.failed == ["x"]


@pytest.mark.anyio
async def test_a_failed_required_task_skips_everything_that_needs_it():
    async def boom(_): raise RuntimeError("no")
    steps, res = await collect([t("x", boom, required=True), t("y", deps=("x",)), t("w", deps=("y",)), t("z")])
    assert res.runs["y"].status == "skipped" and res.runs["w"].status == "skipped" and res.value("z") == "z"
    assert any(s.get("task", {}).get("status") == "skipped" for s in steps)


@pytest.mark.anyio
async def test_retries_are_bounded_and_a_later_success_counts():
    calls = {"n": 0}

    async def flaky(_):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    _, res = await collect([t("f", flaky, retries=2)])
    assert res.runs["f"].status == "done" and res.runs["f"].attempts == 3
    calls["n"] = -10
    _, res2 = await collect([t("f", flaky, retries=1)])
    assert res2.runs["f"].status == "failed" and res2.runs["f"].attempts == 2     # bounded: first try plus one retry


@pytest.mark.anyio
async def test_a_task_that_takes_too_long_is_failed_not_awaited_forever():
    async def hang(_): await asyncio.sleep(5)
    t0 = time.monotonic()
    _, res = await collect([t("h", hang, timeout_s=0.1), t("ok")])
    assert time.monotonic() - t0 < 2 and res.runs["h"].status == "failed" and res.runs["h"].error == "timed out"
    assert res.value("ok") == "ok"


@pytest.mark.anyio
async def test_progress_events_describe_the_plan_and_each_task():
    steps, _ = await collect([t("a"), t("b", deps=("a",))])
    assert steps[0]["title"] == "Plan" and [x["id"] for x in steps[0]["tasks"]] == ["a", "b"]
    statuses = [(s["task"]["id"], s["task"]["status"]) for s in steps if "task" in s]
    assert statuses.index(("a", "done")) < statuses.index(("b", "running"))      # b starts only after a finished
    assert ("b", "done") in statuses


@pytest.mark.anyio
async def test_stopping_early_cancels_running_tasks():
    started = asyncio.Event()
    cancelled = {"v": False}

    async def long(_):
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled["v"] = True
            raise

    gen = run_plan([t("l", long)])
    async for kind, payload in gen:
        if kind == "step" and payload.get("task", {}).get("status") == "running":
            await asyncio.wait_for(started.wait(), 1)
            break
    await gen.aclose()
    await asyncio.sleep(0.05)
    assert cancelled["v"]
