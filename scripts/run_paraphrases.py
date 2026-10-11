"""Measure intent understanding on the held-out paraphrase suite.

    python scripts/run_paraphrases.py --mode rules               # deterministic fallback only (no key needed)
    python scripts/run_paraphrases.py --mode llm --hard          # only the phrasings the rules cannot handle (few calls)
    python scripts/run_paraphrases.py --mode llm --limit 20      # the first 20 phrasings with the configured model
    python scripts/run_paraphrases.py --mode llm                 # all phrasings (needs a quota that allows ~60 calls)

Free Gemini keys allow only a few requests a minute (set GEMINI_RPM in .env to your limit); this script waits for the quota
instead of falling back, and answers are cached in .llm_cache so a second run only repeats the calls that failed.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_MAX_WAIT_S", "90")   # wait out short rate limits here (the app itself fails over after 2 s)

from eval.paraphrases import evaluate  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "llm"], default="rules")
    ap.add_argument("--limit", type=int, default=None, help="run at most N phrasings")
    ap.add_argument("--hard", action="store_true", help="llm mode: only the phrasings the rules are not expected to handle")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args()
    llm = None
    if args.mode == "llm":
        from agent.agent import llm_ready
        from agent.llm import get_llm
        llm = get_llm()
        if not llm_ready(llm):
            sys.exit("No LLM key configured (see .env.example).")

    def progress(n: int, total: int, text: str) -> None:
        if args.mode == "llm":
            print(f"[{n}/{total}] {text}", flush=True)

    res = await evaluate(args.mode, llm, limit=args.limit, hard_only=args.hard, progress=progress)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return
    print(f"mode={res['mode']}  passed {res['passed']}/{res['total']}  accuracy {res['accuracy']:.1%}")
    print("by intent:", res["by_intent"])
    for f in res["failures"]:
        print(f"  FAIL [{f['source']}] {f['text']!r}: {'; '.join(f['problems'])}")


if __name__ == "__main__":
    asyncio.run(main())
