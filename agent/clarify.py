"""Confidence-aware clarification: ask only when the reading changes the answer, and show what each reading would give.

A value such as "SP" can be the customer's state or the seller's state. When the words, the screen and an earlier choice do not
settle it (perceive / interpret record that as `Interp.ambiguities`), the agent measures the headline number under each reading.
If the readings differ materially it asks, showing both numbers; if they agree it carries on with the default silently. The answer
is remembered for the session, so the same question is not asked twice. Everything here is best-effort: any failure keeps the
interpretation as it was.
"""
from __future__ import annotations

import logging
import re

from agent.reasoning.facts import fmt_number
from agent.reasoning.gateway import AnalysisError, Gateway
from agent.schema import AgentMemory, Interp

logger = logging.getLogger("agent.clarify")

_ANALYTIC = ("data", "compare", "explain", "investigate")


def remember_choice(mem: AgentMemory, message: str) -> None:
    """If the message is one of the options just offered, keep that choice for the session. Pending options never outlive a turn."""
    pend = mem.pending_choices
    if pend and message.strip() in pend:
        kind, key = pend[message.strip()]
        mem.prefs[kind] = key
    mem.pending_choices = {}


def option_message(text: str, mention: str, field: str) -> str:
    """The user's own message with the ambiguous value made explicit: 'revenue in SP' -> 'revenue in seller state SP'."""
    phrase = field.replace("_", " ")
    pat = re.compile(rf"(?<!\w){re.escape(mention)}(?!\w)", re.IGNORECASE)
    if pat.search(text):
        return pat.sub(lambda m: f"{phrase} {m.group(0)}", text, count=1)      # keep the user's own spelling and case
    return f"{text} ({phrase} {mention})"


def _as_written(text: str, mention: str) -> str:
    m = re.search(rf"(?<!\w){re.escape(mention)}(?!\w)", text, re.IGNORECASE)
    return m.group(0) if m else mention


async def ambiguity_clarification(ctx, it: Interp) -> bool:
    """Turn `it` into a clarification when it is materially ambiguous. Returns True when it did."""
    try:
        if not getattr(ctx.cfg, "clarify_ambiguity", False) or it.intent not in _ANALYTIC or not it.ambiguities or not it.metric:
            return False
        amb = it.ambiguities[0]                                   # one question at a time
        lex, metric = ctx.lex, it.metric
        mi = lex.metrics[metric]
        gw = Gateway(ctx)
        base = {k: v for k, v in it.filters.items() if k not in amb["options"]}
        measured: list[tuple[str, float | None]] = []
        for key in amb["options"]:
            try:
                res = await gw.query(metric, [], {**base, key: list(amb["values"])}, it.primary)
            except AnalysisError:
                return False                                      # cannot compare the readings: keep the default
            measured.append((key, res.get("total")))
            it.clarify_evidence.append(await gw.evidence("metric_query", metric, {**res, "view": None}))
        vals = [v for _, v in measured if v is not None]
        if len(vals) < 2:
            return False
        lo, hi = min(vals), max(vals)
        gap = None if not lo else 100.0 * (hi - lo) / abs(lo)
        if gap is not None and gap < lex.materiality_pct:
            it.clarify_evidence = []
            return False                                          # the readings agree closely enough: no need to ask

        label = it.primary.label if it.primary else "all available data"
        parts = []
        for key, v in measured:
            line = f"{key.replace('_', ' ')} {', '.join(amb['values'])}: {fmt_number(v, mi.unit)}"
            parts.append(line)
            it.assumptions.append(line)                           # numbers the answer states are allowed text, not stray digits
        options = [option_message(ctx.text, amb["text"], key) for key, _ in measured]
        names = " or the ".join(k.replace("_", " ") for k, _ in measured)
        it.intent = "clarify"
        it.clarification = (f"“{_as_written(ctx.text, amb['text'])}” could be the {names}, and the answer differs "
                            f"({mi.title.lower()}, {label}: " + " vs ".join(parts) + "). Which do you mean? "
                            "I will remember your choice for this session.")
        it.clarify_options = options
        ctx.mem.pending_choices = {o: (amb["kind"], k) for o, (k, _) in zip(options, measured)}
        return True
    except Exception:  # noqa: BLE001
        logger.warning("ambiguity check skipped", exc_info=True)
        return False


_VIEWISH = ("view", "data", "compare", "explain", "investigate")
_LOW_COVERAGE = 0.5


def guard_unmapped(ctx, it: Interp) -> bool:
    """Do not answer confidently when part of the request was not understood.

    * a period that lies entirely after the data ends: say so instead of returning an empty answer;
    * rules-path interpretations (the model was not used): words the parser could not place. If most of the request is unplaced the
      agent asks; otherwise it answers the part it understood and says which words it did not use (the answer is then PARTIAL).
    * values or fields dropped during validation (any path) are carried in `it.ignored`, so the answer is never VERIFIED
      while claiming to cover something it dropped.
    Returns True when the interpretation became a clarification."""
    try:
        if not getattr(ctx.cfg, "guard_unmapped", False) or it.intent not in _VIEWISH:
            return False
        it.ignored = list(getattr(it.assumptions, "ignored", []))
        if it.intent != "view" and it.primary and it.primary.start > ctx.as_of:
            it.intent, it.clarify_options = "clarify", []
            it.clarification = (f"The data in this application ends on {ctx.as_of}, so there is nothing for {it.primary.label}. "
                                "Pick a period inside the data, for example the latest month.")
            return True
        ann = ctx.ann
        if ann is None or it.source == "llm":                    # the model reads other languages and phrasings; the parser cannot judge it
            return False
        words = [w for w in ann.unexplained if len(w) >= 3]
        if not words:
            return False
        shown = ", ".join(f"“{w}”" for w in dict.fromkeys(words))
        if ann.coverage < _LOW_COVERAGE:
            sample = [m.title for m in list(ctx.lex.metrics.values())[:4]]
            it.intent = "clarify"
            it.clarification = (f"I could not map {shown} to anything in this application, so I would be guessing. "
                                "I can open pages, show metrics such as " + ", ".join(sample) + " and explain changes; what would you like?")
            it.clarify_options = [f"Show {s.lower()} last month" for s in sample[:3]]
            return True
        it.ignored += [f"not understood: {w}" for w in dict.fromkeys(words)]
        it.assumptions.append(f"I did not understand {shown}, so the answer does not use it.")
        return False
    except Exception:  # noqa: BLE001
        logger.warning("unmapped-terms guard skipped", exc_info=True)
        return False
