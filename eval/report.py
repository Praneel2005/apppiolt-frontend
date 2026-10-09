"""Benchmark Reporting and Markdown Table Generator (results/table.md)."""
from __future__ import annotations

from typing import Mapping
from eval.metrics import BenchmarkSummary


def format_benchmark_table(
    summaries: Mapping[str, BenchmarkSummary],
) -> str:
    """Generates comparative Markdown benchmark table comparing full system vs ablations & baselines."""
    lines = [
        "# AppPilot Benchmark Results (KPIs K1–K7)",
        "",
        "Evaluation over gold operational tasks on Olist Seller Operations application.",
        "",
        "| System / Variant | K1 Page Acc | K2 State Acc | K3 Success (95% CI) | K3 Steps | K3 p50 Latency | K4 Faithfulness | K5 MRR | K6 Safety Catch | K6 Verify Pass |",
        "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for name, s in summaries.items():
        ci_str = f"[{s.ci_success[0]:.2f}, {s.ci_success[1]:.2f}]"
        row = (
            f"| **{name}** "
            f"| {s.k1_destination_acc * 100:.1f}% "
            f"| {s.k2_state_acc * 100:.1f}% "
            f"| **{s.success_rate * 100:.1f}%** {ci_str} "
            f"| {s.k3_avg_steps:.1f} "
            f"| {s.k3_p50_latency_ms:.0f} ms "
            f"| {s.k4_faithfulness * 100:.1f}% "
            f"| {s.k5_retrieval_mrr:.3f} "
            f"| {s.k6_safety_catch_rate * 100:.1f}% "
            f"| {s.k6_verify_pass_rate * 100:.1f}% |"
        )
        lines.append(row)

    lines.append("")
    lines.append("### Level Breakdown (Full System)")
    if "AppPilot (Full)" in summaries:
        full = summaries["AppPilot (Full)"]
        lines.append("| Level | Description | Success Rate |")
        lines.append("|:---|:---|:---:|")
        level_names = {
            "L1": "Navigation / Routing",
            "L2": "State & Categorical Filters",
            "L3": "Date Presets & Sort",
            "L4": "Analytics & Faithfulness",
            "L5": "Multi-Step & Writes",
            "L6": "Safety & Guardrails",
        }
        for lvl, rate in full.level_breakdown.items():
            lines.append(f"| **{lvl}** | {level_names.get(lvl, lvl)} | {rate * 100:.1f}% |")

    return "\n".join(lines)
