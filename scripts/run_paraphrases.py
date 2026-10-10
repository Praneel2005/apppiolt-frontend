"""Measure intent understanding on the held-out paraphrase suite.

    python scripts/run_paraphrases.py --mode rules      # deterministic fallback only (no key needed)
    python scripts/run_paraphrases.py --mode llm        # with the configured provider (needs a key in .env)
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.paraphrases import evaluate  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "llm"], default="rules")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args()
    llm = None
    if args.mode == "llm":
        from agent.agent import llm_ready
        from agent.llm import get_llm
        llm = get_llm()
        if not llm_ready(llm):
            sys.exit("No LLM key configured (see .env.example).")
    res = await evaluate(args.mode, llm)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return
    print(f"mode={res['mode']}  passed {res['passed']}/{res['total']}  accuracy {res['accuracy']:.1%}")
    print("by intent:", res["by_intent"])
    for f in res["failures"]:
        print(f"  FAIL [{f['source']}] {f['text']!r}: {'; '.join(f['problems'])}")


if __name__ == "__main__":
    asyncio.run(main())
