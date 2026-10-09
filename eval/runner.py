"""Benchmark evaluation runner for AppPilot (Part K).

Executes tasks across the agent or baseline models on isolated sessions,
recording real-time telemetry, verification status, and metric outputs.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from backend.sessions import create_session, get_session
from contracts.actions import AgentConfig
from eval.metrics import (
    BenchmarkSummary,
    TaskMetricResult,
    aggregate_benchmark_results,
    compute_task_metrics,
)
from eval.tasks import GoldTask

logger = logging.getLogger("eval.runner")


async def run_evaluation(
    agent: Any,
    tasks: list[GoldTask],
    config: AgentConfig | None = None,
) -> tuple[BenchmarkSummary, list[TaskMetricResult]]:
    """Runs a suite of GoldTasks through an agent and produces benchmark evaluation metrics."""
    cfg = config or AgentConfig()
    results: list[TaskMetricResult] = []

    for task in tasks:
        # Create isolated session
        session = create_session()
        sid = session.id

        t0 = time.time()
        events_list: list[dict] = []
        err_msg: str | None = None

        try:
            async for ev in agent.handle(sid, task.query, cfg):
                events_list.append({"type": ev.type, "data": ev.data})
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            logger.error(f"Task {task.task_id} failed with error: {e}")

        elapsed_ms = (time.time() - t0) * 1000.0

        # Retrieve state after execution
        s_after = get_session(sid)
        final_state_dict = s_after.state.model_dump() if s_after.state else None
        evidence_dict = s_after.evidence

        metric_res = compute_task_metrics(
            task=task,
            events=events_list,
            final_state=final_state_dict,
            evidence_map=evidence_dict,
            latency_ms=elapsed_ms,
        )
        if err_msg:
            metric_res.error_message = err_msg
            metric_res.success = False

        results.append(metric_res)

    summary = aggregate_benchmark_results(results)
    return summary, results
