"""Ablation: turn one layer of the agent off at a time and measure what breaks.

Each variant switches off a single, real layer (each switch is wired in the agent; none is cosmetic) and re-runs the groups that
layer is meant to protect. The point is the difference from the full system, measured on the same cases in the same run.

  full             everything on
  no_typo_repair   the parser no longer repairs misspelt words           -> typos
  no_guard         half-understood requests are answered as if understood  -> unanswerable (false answers)
  no_clarify       ambiguous values are silently assumed                   -> ambiguity (silent assumptions)
  no_verifier      the agent trusts that its screen action worked          -> fault (silent failures)
"""
from __future__ import annotations

VARIANTS: dict[str, dict] = {
    "full": {},
    "no_typo_repair": {"repair_typos": False},
    "no_guard": {"guard_unmapped": False},
    "no_clarify": {"clarify_ambiguity": False},
    "no_verifier": {"use_verifier": False},
}
GROUPS = ["typos", "unanswerable", "ambiguity", "fault"]


def _frac(pair: tuple[int, int]) -> str:
    bad, n = pair
    return f"{bad}/{n}" if n else "n/a"


async def run_ablation(progress=None) -> dict:
    from eval.robustness import evaluate

    out: dict[str, dict] = {}
    for name, overrides in VARIANTS.items():
        if progress:
            progress(name)
        out[name] = await evaluate("rules", groups=GROUPS, cfg_overrides=overrides)
    return out


def to_markdown(results: dict[str, dict]) -> str:
    rows = ["| variant | typos understood | false answers (unanswerable) | silent assumptions (ambiguous) | silent failures (faulted screen) |",
            "|---|---|---|---|---|"]
    for name, r in results.items():
        typos = r["by_group"].get("typos", "n/a")
        rows.append(f"| {name} | {typos} | {_frac(r['false_answer'])} | {_frac(r['silent_assumption'])} | {_frac(r['silent_failure'])} |")
    return "\n".join(rows)
