"""Proof records: for every piece of evidence, the exact governed call that produced it, so any number can be re-checked.

Nothing here changes how answers are made. The Gateway tags each metric result with the call that produced it, the proof is
stored beside the evidence, and `replay` runs the same call again and compares the numbers. All of it is best-effort: a
failure to record or replay never affects an answer (the proof is simply absent or reported as unavailable).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

logger = logging.getLogger("agent.reasoning.proof")

CALL_KEY = "_call"                      # private key carried on a metric result; removed before it becomes evidence
_SKIP_KEYS = {"view", "source", "definition", "note", "untrusted_fields", "untrusted_note", CALL_KEY}
_REL_TOL = 1e-9
_ABS_TOL = 1e-6


def make_call(op: str, request: dict[str, Any]) -> dict[str, Any]:
    """`op` is query | compare | trend; `request` is the JSON body that was sent to the metric route."""
    return {"op": op, "request": _jsonable(request)}


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def record(session, evidence_id: str, kind: str, source: str, values: dict[str, Any], call: dict[str, Any] | None,
           calls_in_turn: list[dict[str, Any]] | None = None) -> None:
    """Store the proof for one evidence item. Never raises."""
    try:
        proofs = getattr(session, "proofs", None)
        if proofs is None:
            return
        src = values.get("source") if isinstance(values.get("source"), dict) else {}
        proofs[evidence_id] = {
            "evidence_id": evidence_id, "kind": kind, "source": source,
            "exact": call is not None,
            "call": call,
            # a derived block (several queries combined) lists the calls of the turn instead of pretending to one call
            "calls": [] if call is not None else [c for c in (calls_in_turn or [])][-8:],
            "metric": {"id": values.get("metric"), "title": values.get("title"), "unit": values.get("unit"),
                       "additive": values.get("additive"), "definition": values.get("definition")},
            "filters": values.get("filters"), "group_by": values.get("group_by"),
            "period": values.get("period") or {k: values.get(k) for k in ("current", "previous") if values.get(k)} or None,
            "row_count": values.get("row_count") if isinstance(values.get("row_count"), int)
            else (len(values["rows"]) if isinstance(values.get("rows"), list) else None),
            "data_layers": src.get("data_layers") or [],
            "tables": src.get("tables") or [],
            "view": values.get("view") if isinstance(values.get("view"), dict) else None,
            "recorded_at": time.time(),
        }
    except Exception:  # noqa: BLE001
        logger.debug("proof not recorded for %s", evidence_id, exc_info=True)


# ------------------------------------------------------------------------------------ replay
def _numeric_leaves(obj: Any, path: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and not math.isfinite(obj):
            return out
        out[path] = float(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SKIP_KEYS:
                continue
            out.update(_numeric_leaves(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            # rows are matched by their labels (the text fields), not by position, so a re-ordered or re-shaped list cannot
            # produce a false difference
            label = "|".join(str(x) for x in v.values() if isinstance(x, str)) if isinstance(v, dict) else ""
            key = f"[{label}]" if label else f"[{i}]"
            for p2, val in _numeric_leaves(v, f"{path}{key}").items():
                out.setdefault(p2, val)
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_ABS_TOL, _REL_TOL * max(abs(a), abs(b)))


def compare_values(stored: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Numeric leaves of the stored evidence against a fresh run. Lists in the evidence may be trimmed, so only paths that exist
    in the stored copy are compared."""
    a, b = _numeric_leaves(stored), _numeric_leaves(fresh)
    checked, diffs = 0, []
    for path, v in a.items():
        if path not in b:
            continue
        checked += 1
        if not _close(v, b[path]):
            diffs.append({"path": path, "stored": v, "now": b[path]})
    return {"checked": checked, "differences": diffs[:10], "match": checked > 0 and not diffs}


_ROUTES = {"query": ("MetricQuery", "metrics_query"), "compare": ("MetricCompare", "metrics_compare"),
           "trend": ("TrendQuery", "metrics_trend")}


async def replay(session, evidence_id: str) -> dict[str, Any]:
    """Run the recorded call again and compare it with what was shown. Returns a plain dict; never raises."""
    import asyncio

    proof = (getattr(session, "proofs", {}) or {}).get(evidence_id)
    ev = session.evidence.get(evidence_id)
    if proof is None or ev is None:
        return {"ok": False, "reason": "no_proof", "message": f"No recorded proof for {evidence_id}."}
    call = proof.get("call")
    if not call:
        return {"ok": False, "reason": "derived",
                "message": "This evidence combines several queries, so it has no single call to replay. "
                           "Each underlying query is listed in the proof."}
    try:
        from backend.routes import metrics as m  # local import: keeps the agent importable without the backend routes

        model_name, fn_name = _ROUTES[call["op"]]
        body = getattr(m, model_name)(**call["request"])
        fresh = await asyncio.to_thread(getattr(m, fn_name), body)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "replay_failed", "message": f"{type(e).__name__}: {str(e)[:160]}"}
    cmp = compare_values(ev.values, fresh)
    return {"ok": True, "evidence_id": evidence_id, "replayed_at": time.time(), **cmp,
            "message": ("Same result: the numbers were re-computed from the database and match." if cmp["match"] else
                        "The re-computed numbers differ from what was shown." if cmp["differences"] else
                        "Nothing comparable was found in this evidence.")}


# ------------------------------------------------------------------------------------ claims
import re  # noqa: E402

_FACT_RE = re.compile(r"\{(F\d+)\}")


def _fact_in(value: float, leaves: dict[str, float]) -> bool:
    for v in leaves.values():
        if _close(value, v) or abs(value - v) <= 0.051 or abs(abs(value) - abs(v)) <= 0.051 \
                or (v and abs(abs(value) - abs(v)) / abs(v) <= 1e-3):
            return True
    return False


def fact_support(fact, session) -> str:
    """direct = the number appears in its evidence; derived = computed from evidence numbers (not literally there);
    none = the fact carries no evidence (text only, or evidence missing)."""
    try:
        ev = session.evidence.get(fact.evidence) if fact.evidence else None
        if ev is None:
            return "none"
        if fact.value is None:
            return "derived"
        return "direct" if _fact_in(float(fact.value), _numeric_leaves(ev.values)) else "derived"
    except Exception:  # noqa: BLE001
        return "none"


def build_claims(fs, template: str, session) -> list[dict[str, Any]]:
    """One claim per line of the answer: its rendered text, the facts it states, and the evidence behind each. Never raises."""
    claims: list[dict[str, Any]] = []
    try:
        for line in template.split("\n"):
            ids = list(dict.fromkeys(_FACT_RE.findall(line)))
            facts = [fs.facts[i] for i in ids if i in fs.facts]
            if not facts:
                continue
            evidence = list(dict.fromkeys(f.evidence for f in facts if f.evidence))
            claims.append({"text": fs.render(line).strip(), "facts": [f.id for f in facts], "evidence": evidence,
                           "support": {f.id: fact_support(f, session) for f in facts}})
    except Exception:  # noqa: BLE001
        logger.debug("claims not built", exc_info=True)
    return claims
