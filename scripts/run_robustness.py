"""Measure robustness and hallucination: typos, other languages, injected instructions, unanswerable questions, grounding, actions.

    python scripts/run_robustness.py --mode rules                       # no key needed; the multilingual group is skipped
    python scripts/run_robustness.py --mode llm --delay 13              # with the configured model (spaces calls for a free-tier quota)
    python scripts/run_robustness.py --mode rules --groups injection unanswerable
    python scripts/run_robustness.py --mode rules --json > results/robustness.json

Needs the database (the agent answers from it). Numbers printed here are measurements of THIS run: quote them with the mode.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_MAX_WAIT_S", "90")   # wait out short rate limits here instead of falling back

from eval.robustness import evaluate  # noqa: E402


def _pct(pair) -> str:
    bad, n = pair
    return f"{bad}/{n}" + (f" = {100 * bad / n:.1f}%" if n else " (no cases)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "llm"], default="rules")
    ap.add_argument("--groups", nargs="*", default=None, help="typos multilingual injection unanswerable grounding action")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait between cases (free-tier quotas)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    llm = None
    if args.mode == "llm":
        from agent.agent import llm_ready
        from agent.llm import get_llm
        llm = get_llm()
        if not llm_ready(llm):
            sys.exit("No LLM key configured (see .env.example).")

    def progress(n: int, total: int, text: str) -> None:
        print(f"[{n}/{total}] {text}", file=sys.stderr, flush=True)

    res = await evaluate(args.mode, llm, args.groups, args.delay, progress)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=list))
        return
    print(f"\nmode={res['mode']}  passed {res['passed']}/{res['total']}")
    print("by group:", res["by_group"])
    print("--- headline measures (this run) ---")
    print(f"injection success rate     {_pct(res['injection_success'])}   (target 0)")
    print(f"false-answer rate          {_pct(res['false_answer'])}   (unanswerable questions answered confidently)")
    print(f"ungrounded-number rate     {_pct(res['ungrounded_numbers'])}   (answers with a number that has no source)")
    print(f"action hallucination rate  {_pct(res['action_hallucination'])}   (validator rejected something invented)")
    print(f"calibration error (ECE)    {res['ece'] if res['ece'] is not None else 'n/a (confidence does not vary in this mode)'}")
    for f in res["failures"]:
        print(f"  FAIL [{f['group']}] {f['text']!r}: {'; '.join(f['problems'])}")


if __name__ == "__main__":
    asyncio.run(main())
