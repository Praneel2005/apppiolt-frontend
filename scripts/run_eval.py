"""CLI script to run the AppPilot benchmark and generate results/table.md.

Systems compared (all on the same gold tasks):
  AppPilot V3 (layered)  - the production agent;  V3 without LLM - rules + deterministic composition only
  B3 tool-plan agent     - the previous LLM tool-planner (agent/legacy_agent.py)
  B1 prompt stuffing / B2 RAG + tools without gates - the original baselines
Numbers are produced by this run only; nothing here is a pre-computed result.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent import AppPilotAgent  # noqa: E402
from agent.baselines import BaselineB1Agent, BaselineB2Agent  # noqa: E402
from agent.legacy_agent import LegacyToolPlanAgent  # noqa: E402
from contracts.actions import AgentConfig  # noqa: E402
from eval import format_benchmark_table, generate_gold_tasks, run_evaluation  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description="Run AppPilot Evaluation Benchmark")
    parser.add_argument("--count", type=int, default=30, help="Number of gold tasks to evaluate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for gold tasks")
    parser.add_argument("--out", type=str, default="results/table.md", help="Output markdown path")
    parser.add_argument("--skip-llm-baselines", action="store_true", help="only run the V3 systems")
    args = parser.parse_args()

    tasks = generate_gold_tasks(count=args.count, seed=args.seed)
    print(f"Loaded {len(tasks)} tasks (seed={args.seed}).")
    cfg = AgentConfig(auto_confirm_writes=True)  # evaluation only: simulated user approval
    summaries = {}

    print("\n[1] AppPilot V3 (layered, with LLM if a key is configured)...")
    summaries["AppPilot V3"], _ = await run_evaluation(AppPilotAgent(), tasks, cfg)
    print(f"  -> success {summaries['AppPilot V3'].success_rate * 100:.1f}%")

    print("\n[2] AppPilot V3 without LLM (rules + deterministic composition)...")
    summaries["AppPilot V3, rules only"], _ = await run_evaluation(AppPilotAgent(use_llm=False), tasks, cfg)
    print(f"  -> success {summaries['AppPilot V3, rules only'].success_rate * 100:.1f}%")

    if not args.skip_llm_baselines:
        for name, agent, c in (("B3 tool-plan agent", LegacyToolPlanAgent(), AgentConfig(auto_confirm_writes=True)),
                               ("B1 prompt stuffing", BaselineB1Agent(), None),
                               ("B2 RAG + tools, no gates", BaselineB2Agent(), None)):
            print(f"\n[*] {name}...")
            summaries[name], _ = await run_evaluation(agent, tasks, c) if c else await run_evaluation(agent, tasks)
            print(f"  -> success {summaries[name].success_rate * 100:.1f}%")

    table_md = format_benchmark_table(summaries)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(table_md, encoding="utf-8")
    print(f"\nSaved benchmark results to {out_path.resolve()}\n")
    print(table_md)


if __name__ == "__main__":
    asyncio.run(main())
