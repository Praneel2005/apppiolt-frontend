"""Turn one layer off at a time and measure the damage (rules mode, no model needed; needs the database).

    python scripts/run_ablation.py                  # prints a markdown table
    python scripts/run_ablation.py --out results/ablation.md

Lower is better in every column except 'typos understood'. Quote the table as measured on this run's cases (see eval/robustness.py).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.ablation import run_ablation, to_markdown  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="also write the table to this file")
    args = ap.parse_args()
    res = await run_ablation(progress=lambda name: print(f"running variant: {name}", file=sys.stderr, flush=True))
    table = to_markdown(res)
    print("\n" + table)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(table + "\n", encoding="utf-8")
        print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
