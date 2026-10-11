"""Typed task plans: tasks with dependencies, run in parallel where they are independent, with timeouts and bounded retries.

A plan is a list of `Task`s. Each task has an id, the ids it depends on, a coroutine function that receives the results of its
dependencies, and a `kind` (checked against an allow-list when one is given, so a plan proposed by a model can only contain task types
the code knows). `run_plan` is an async generator: it yields progress events ("step", {...}) as tasks start and finish, and finally
("result", PlanResult). A failed task never takes the plan down: its dependants are skipped when it was required, or receive None when
it was optional. Nothing here knows about analytics; the analysis engine builds its plan from these pieces.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

logger = logging.getLogger("agent.dag")

TaskFn = Callable[[dict[str, Any]], Awaitable[Any]]


class PlanError(ValueError):
    """The plan itself is invalid (duplicate id, unknown dependency, cycle, disallowed task kind)."""


@dataclass
class Task:
    id: str
    title: str
    fn: TaskFn
    deps: tuple[str, ...] = ()
    kind: str = "task"
    retries: int = 0                # extra attempts after the first failure
    timeout_s: float = 60.0
    required: bool = False          # True: dependants are skipped if this fails; False: they run with None for its result


@dataclass
class TaskRun:
    id: str
    status: str                     # done | failed | skipped
    value: Any = None
    error: str | None = None
    attempts: int = 0
    ms: float = 0.0


@dataclass
class PlanResult:
    runs: dict[str, TaskRun] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)       # the order tasks finished in
    elapsed_ms: float = 0.0

    def value(self, task_id: str) -> Any:
        r = self.runs.get(task_id)
        return r.value if r and r.status == "done" else None

    @property
    def failed(self) -> list[str]:
        return [i for i, r in self.runs.items() if r.status == "failed"]

    @property
    def skipped(self) -> list[str]:
        return [i for i, r in self.runs.items() if r.status == "skipped"]


# ------------------------------------------------------------------------------------ validation
def validate_plan(tasks: list[Task], allowed_kinds: set[str] | None = None) -> list[list[str]]:
    """Check a plan and return its levels (tasks that could run together). Raises PlanError for anything invalid."""
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise PlanError("duplicate task ids: " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
    known = set(ids)
    for t in tasks:
        if t.id in t.deps:
            raise PlanError(f"task '{t.id}' depends on itself")
        missing = [d for d in t.deps if d not in known]
        if missing:
            raise PlanError(f"task '{t.id}' depends on unknown task(s): {', '.join(missing)}")
        if allowed_kinds is not None and t.kind not in allowed_kinds:
            raise PlanError(f"task '{t.id}' has a kind that is not allowed: '{t.kind}'")
    remaining = {t.id: set(t.deps) for t in tasks}
    levels: list[list[str]] = []
    while remaining:
        ready = sorted(i for i, deps in remaining.items() if not deps)
        if not ready:
            raise PlanError("the plan has a dependency cycle among: " + ", ".join(sorted(remaining)))
        levels.append(ready)
        for i in ready:
            del remaining[i]
        for deps in remaining.values():
            deps.difference_update(ready)
    return levels


# ------------------------------------------------------------------------------------ running
def _describe(tasks: list[Task]) -> list[dict[str, Any]]:
    return [{"id": t.id, "title": t.title, "deps": list(t.deps)} for t in tasks]


async def run_plan(tasks: list[Task], *, max_parallel: int = 4, allowed_kinds: set[str] | None = None) -> AsyncIterator[tuple[str, Any]]:
    """Run the plan. Yields ("step", payload) events while it runs and ("result", PlanResult) at the end."""
    levels = validate_plan(tasks, allowed_kinds)
    by_id = {t.id: t for t in tasks}
    t0 = time.monotonic()
    yield "step", {"title": "Plan", "detail": f"{len(tasks)} tasks in {len(levels)} stage(s), up to {max_parallel} at a time",
                   "tasks": _describe(tasks)}

    result = PlanResult()
    events: asyncio.Queue = asyncio.Queue()
    sem = asyncio.Semaphore(max(1, max_parallel))
    running: dict[str, asyncio.Task] = {}
    started: set[str] = set()

    async def execute(t: Task) -> None:
        inputs = {d: result.runs[d].value for d in t.deps}
        async with sem:
            await events.put(("step", {"title": t.title, "detail": "", "task": {"id": t.id, "status": "running"}}))
            started_at = time.monotonic()
            attempts, run = 0, None
            while run is None:
                attempts += 1
                try:
                    value = await asyncio.wait_for(t.fn(inputs), t.timeout_s)
                    run = TaskRun(t.id, "done", value, None, attempts)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001  (one task failing must not stop the others)
                    if attempts > t.retries:
                        err = "timed out" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
                        run = TaskRun(t.id, "failed", None, err[:200], attempts)
                    else:
                        logger.info("task %s failed (attempt %d), retrying: %s", t.id, attempts, e)
            run.ms = round((time.monotonic() - started_at) * 1000.0, 1)
            result.runs[t.id] = run
            result.order.append(t.id)
            await events.put(("step", {"title": t.title, "detail": run.error or "", "task": {
                "id": t.id, "status": run.status, "ms": run.ms, "attempts": run.attempts}}))

    def launch_ready() -> None:
        progressed = True
        while progressed:
            progressed = False
            for t in tasks:
                if t.id in started or any(d not in result.runs for d in t.deps):
                    continue
                started.add(t.id)
                progressed = True
                blocked = [d for d in t.deps if result.runs[d].status == "skipped" or
                           (result.runs[d].status == "failed" and by_id[d].required)]
                if blocked:                                   # a dependency this task cannot do without did not complete
                    result.runs[t.id] = TaskRun(t.id, "skipped", None, f"needs {', '.join(blocked)}")
                    result.order.append(t.id)
                    events.put_nowait(("step", {"title": t.title, "detail": f"skipped: needs {', '.join(blocked)}",
                                                "task": {"id": t.id, "status": "skipped"}}))
                else:
                    running[t.id] = asyncio.ensure_future(execute(t))

    try:
        while True:
            launch_ready()
            while not events.empty():
                yield events.get_nowait()
            if not running:
                break
            done, _ = await asyncio.wait(list(running.values()), timeout=0.05, return_when=asyncio.FIRST_COMPLETED)
            for tid in [i for i, f in running.items() if f in done]:
                fut = running.pop(tid)
                if fut.exception() is not None:               # execute() catches everything; this is a bug in the scheduler itself
                    raise fut.exception()  # type: ignore[misc]
        while not events.empty():
            yield events.get_nowait()
    finally:
        for fut in running.values():                           # the consumer stopped early: do not leave work running
            fut.cancel()
    result.elapsed_ms = round((time.monotonic() - t0) * 1000.0, 1)
    yield "result", result
