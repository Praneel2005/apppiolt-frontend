"""AppPilot Evaluation Harness (Part K)."""
from eval.metrics import BenchmarkSummary, TaskMetricResult, aggregate_benchmark_results, compute_task_metrics
from eval.report import format_benchmark_table
from eval.runner import run_evaluation
from eval.tasks import GoldTask, generate_gold_tasks

__all__ = [
    "GoldTask",
    "generate_gold_tasks",
    "compute_task_metrics",
    "aggregate_benchmark_results",
    "run_evaluation",
    "format_benchmark_table",
    "BenchmarkSummary",
    "TaskMetricResult",
]
