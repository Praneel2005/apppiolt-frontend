"""Statistical rigour for "why did X change": is the move unusual, does the decomposition add up, what if the top driver had not moved.

Pure arithmetic over numbers the metric routes already returned; no LLM, no SQL. Every function degrades to None / "insufficient"
instead of guessing, and `build_rigor` never raises: a failure here must not stop the answer.

  * abnormal_change   robust (median / MAD) comparison of this period-to-period change with the previous ones
  * counterfactual    "if the top group had stayed at its old level, the change would have been ..." (additive metrics only)
  * interaction       volume x rate effects with the interaction term shown explicitly, so the split is not order-dependent
  * reconcile_checks  the parts add up to the headline change, or the answer says they do not
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.reasoning.rigor")

MIN_HISTORY = 6          # fewer earlier changes than this: say "not enough history", never a verdict
UNUSUAL_Z = 2.0          # robust z-score beyond which a change is called unusual
_MAD_TO_SD = 1.4826      # makes the MAD comparable to a standard deviation for roughly normal data
_SUM_TOL = 0.005         # parts must add up to the whole within 0.5%


@dataclass
class Rigor:
    abnormal: dict | None = None
    counterfactual: dict | None = None
    interaction: dict | None = None
    checks: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------------------------ is it unusual?
def pct_changes(values: list[float | None]) -> list[float]:
    """Percentage change between consecutive values, skipping gaps and zero bases."""
    out: list[float] = []
    for a, b in zip(values, values[1:]):
        if a is None or b is None or a == 0:
            continue
        out.append(100.0 * (b - a) / abs(a))
    return out


def abnormal_change(change_pct: float | None, history: list[float]) -> dict[str, Any]:
    """Compare one period-to-period change with the earlier ones. Robust to a few extreme months (median / MAD)."""
    n = len(history)
    if change_pct is None:
        return {"verdict": "insufficient", "n": n, "reason": "no percentage change"}
    if n < MIN_HISTORY:
        return {"verdict": "insufficient", "n": n, "reason": "not enough earlier periods to judge"}
    center = statistics.median(history)
    mad = statistics.median(abs(h - center) for h in history)
    scale = max(_MAD_TO_SD * mad, 0.5)                      # a floor: a perfectly flat history must not make everything "unusual"
    z = (change_pct - center) / scale
    lo, hi = min(history), max(history)
    typical = statistics.median(abs(h) for h in history)
    if abs(z) >= UNUSUAL_Z:
        verdict = "unusual"
    else:
        verdict = "within"
    beyond = change_pct > hi or change_pct < lo
    return {"verdict": verdict, "n": n, "z": round(z, 2), "center_pct": round(center, 2), "low_pct": round(lo, 2),
            "high_pct": round(hi, 2), "typical_abs_pct": round(typical, 2), "beyond_history": bool(beyond),
            "change_pct": round(change_pct, 2)}


# ------------------------------------------------------------------------------------ counterfactual
def counterfactual(additive: bool, current: float, previous: float, scan_rows: list[dict], dim: str, top: dict) -> dict | None:
    """If the top mover had stayed at its previous level, what would the headline change have been? Exact for additive metrics."""
    if not additive or not previous:
        return None
    d = top.get("delta")
    if d is None:
        return None
    cur_without = current - float(d)
    return {"dim": dim, "group": str(top.get("group")), "group_delta": float(d),
            "pct_with": 100.0 * (current - previous) / abs(previous),
            "pct_without": 100.0 * (cur_without - previous) / abs(previous),
            "delta_without": cur_without - previous,
            "share_of_change": None if current == previous else 100.0 * float(d) / (current - previous)}


# ------------------------------------------------------------------------------------ volume x rate with the interaction term
def interaction(identity: dict | None) -> dict | None:
    """revenue = orders x average order value. Split the change as volume (at old value), rate (at old volume) and the
    interaction between them, so the answer does not depend on which factor is taken first."""
    if not identity:
        return None
    try:
        n0, n1 = identity["volume"]["previous"], identity["volume"]["current"]
        a0, a1 = identity["rate"]["previous"], identity["rate"]["current"]
        if None in (n0, n1, a0, a1):
            return None
        vol, rate, inter = (n1 - n0) * a0, (a1 - a0) * n0, (n1 - n0) * (a1 - a0)
        total = n1 * a1 - n0 * a0
        return {"volume": vol, "rate": rate, "interaction": inter, "total": total,
                "interaction_share": None if not total else 100.0 * abs(inter) / abs(total)}
    except (KeyError, TypeError):
        return None


# ------------------------------------------------------------------------------------ the parts add up
def _close(a: float, b: float, tol: float = _SUM_TOL) -> bool:
    return abs(a - b) <= max(1e-6, tol * max(abs(a), abs(b)))


def reconcile_checks(delta: float, identity: dict | None, inter: dict | None, scans: list[Any]) -> list[dict]:
    checks: list[dict] = []
    if identity and inter is not None:
        ok = _close(inter["volume"] + inter["rate"] + inter["interaction"], delta)
        checks.append({"name": "factors", "ok": ok,
                       "detail": "the volume, value and interaction effects add up to the headline change" if ok else
                                 "the volume and value effects do not add up to the headline change"})
    for sc in scans[:2]:
        if getattr(sc, "mode", "") != "additive" or not delta:
            continue
        covered = abs(sc.tail) / abs(delta)
        ok = covered <= 0.01
        checks.append({"name": f"groups:{sc.dim}", "ok": ok, "uncovered_pct": round(100 * covered, 2),
                       "detail": f"the {sc.dim.replace('_', ' ')} groups add up to the headline change" if ok else
                                 f"the {sc.dim.replace('_', ' ')} groups do not account for all of the change"})
    return checks


# ------------------------------------------------------------------------------------ assembly
def _calendar_month(start: str, end: str) -> bool:
    return start[8:10] == "01" and start[:7] == end[:7] and end[8:10] >= "28"


def _consecutive_months(baseline_start: str, primary_start: str) -> bool:
    by, bm, py, pm = int(baseline_start[:4]), int(baseline_start[5:7]), int(primary_start[:4]), int(primary_start[5:7])
    return by * 12 + bm + 1 == py * 12 + pm


async def build_rigor(gw, res, trend: dict | None) -> Rigor | None:
    """Everything above for one ExplainResult. Records one piece of evidence per block. Never raises."""
    try:
        out = Rigor()
        # unusual against earlier months: only for month-over-month comparisons whose history we have
        p, b = res.primary, res.baseline
        series, labels = (trend or {}).get("series"), (trend or {}).get("series_labels")
        if (series and labels and res.pct is not None and _calendar_month(p.start, p.end) and _calendar_month(b.start, b.end)
                and _consecutive_months(b.start, p.start) and str(labels[-1]).startswith(p.start[:7])):
            hist = pct_changes(series[:-1])
            out.abnormal = abnormal_change(res.pct, hist)
            out.abnormal["history"] = [round(h, 2) for h in hist]
            if out.abnormal["verdict"] != "insufficient":
                out.abnormal["evidence_id"] = await gw.evidence("comparison", res.metric, {
                    "rows": [{"period": f"change {i + 1}", "pct_change": round(h, 2)} for i, h in enumerate(hist)] +
                            [{"period": p.label, "pct_change": round(res.pct, 2)}],
                    "note": "this change against the previous month-to-month changes",
                    **{k: v for k, v in out.abnormal.items() if k in ("n", "low_pct", "high_pct", "typical_abs_pct", "center_pct")}})
        # what if the top driver had not moved
        sc0 = res.scans[0] if res.scans else None
        if sc0 and sc0.mode == "additive" and sc0.movers and sc0.gross:
            top = sc0.movers[0]
            if abs(top["delta"]) / sc0.gross >= 0.2:
                cf = counterfactual(res.additive, res.current, res.previous, sc0.rows, sc0.dim, top)
                if cf:
                    cf["evidence_id"] = sc0.evidence_id
                    out.counterfactual = cf
        out.interaction = interaction(res.identity)
        out.checks = reconcile_checks(res.delta, res.identity, out.interaction, res.scans)
        return out
    except Exception:  # noqa: BLE001
        logger.warning("rigour checks skipped", exc_info=True)
        return None
