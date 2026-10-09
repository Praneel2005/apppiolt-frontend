"""CLI script to run AppPilot benchmark evaluation and generate results/table.md."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent import AppPilotAgent
from agent.baselines import BaselineB0Agent, BaselineB1Agent, BaselineB2Agent
from contracts.actions import AgentConfig
from eval import format_benchmark_table, generate_gold_tasks, run_evaluation


async def main():
    parser = argparse.ArgumentParser(description="Run AppPilot Evaluation Benchmark")
    parser.add_argument("--count", type=int, default=30, help="Number of gold tasks to evaluate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for gold tasks")
    parser.add_argument("--out", type=str, default="results/table.md", help="Output markdown path")
    args = parser.parse_args()

    print(f"Generating {args.count} gold tasks (seed={args.seed})...")
    tasks = generate_gold_tasks(count=args.count, seed=args.seed)
    print(f"Loaded {len(tasks)} tasks.")

    summaries = {}

    # 1. Full Agent
    print("\n[1/5] Evaluating Full AppPilot Agent...")
    full_agent = AppPilotAgent()
    summary_full, _ = await run_evaluation(full_agent, tasks, AgentConfig())
    summaries["AppPilot (Full)"] = summary_full
    print(f"  -> Success Rate: {summary_full.success_rate * 100:.1f}%")

    # 2. Ablation: No Validator
    print("\n[2/5] Evaluating Ablation: Validator Disabled...")
    summary_noval, _ = await run_evaluation(full_agent, tasks, AgentConfig(use_validator=False))
    summaries["Ablation: No Validator"] = summary_noval
    print(f"  -> Success Rate: {summary_noval.success_rate * 100:.1f}%")

    # 3. Ablation: No Verifier
    print("\n[3/5] Evaluating Ablation: Verifier Disabled...")
    summary_nover, _ = await run_evaluation(full_agent, tasks, AgentConfig(use_verifier=False))
    summaries["Ablation: No Verifier"] = summary_nover
    print(f"  -> Success Rate: {summary_nover.success_rate * 100:.1f}%")

    # 4. Baseline B1: Prompt Stuffing
    print("\n[4/5] Evaluating Baseline B1 (Prompt Stuffing)...")
    b1_agent = BaselineB1Agent()
    summary_b1, _ = await run_evaluation(b1_agent, tasks)
    summaries["Baseline B1 (Prompt Stuffing)"] = summary_b1
    print(f"  -> Success Rate: {summary_b1.success_rate * 100:.1f}%")

    # 5. Baseline B2: RAG + Tools Without Gates
    print("\n[5/5] Evaluating Baseline B2 (RAG No Gates)...")
    b2_agent = BaselineB2Agent()
    summary_b2, _ = await run_evaluation(b2_agent, tasks)
    summaries["Baseline B2 (RAG No Gates)"] = summary_b2
    print(f"  -> Success Rate: {summary_b2.success_rate * 100:.1f}%")

    # Generate Markdown Table
    table_md = format_benchmark_table(summaries)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(table_md, encoding="utf-8")
    print(f"\nSaved benchmark results to {out_path.resolve()}\n")
    print(table_md)


if __name__ == "__main__":
    asyncio.run(main())
