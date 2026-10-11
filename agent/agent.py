"""AppPilot agent V3: a layered, intent-based pipeline.

    L0 context -> L1 perceive -> L2 interpret -> L3 route -> L4 skill (View | Data | Reason | Act | Converse)
               -> L5 verify (UI acknowledgement, honest status) -> L6 answer (facts, optional verified interpretation)

Interface (unchanged): `async def handle(session_id, message, config) -> AsyncIterator[AgentEvent]`.
Events: understanding, understood, frame, retrieval, plan, analysis_step, form_open, form_patch, confirm_request, action,
verify, evidence, canvas, status, answer, error, done.
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from agent.capabilities import get_capability_model
from agent.clarify import ambiguity_clarification, guard_unmapped, remember_choice
from agent.interpret import interpret, llm_interpret, reconcile
from agent.lexicon import get_lexicon
from agent.perceive import perceive
from agent.llm import ChainedLLMProvider, FakeLLM, LLMProvider, get_llm_pair
from agent.schema import Interp, TurnContext, get_agent_memory
from agent.skills.act import run_act
from agent.skills.base import SkillResult
from agent.skills.converse import run_converse
from agent.skills.data import run_data
from agent.skills.entity import run_lookup
from agent.skills.reason import run_reason
from agent.skills.view import run_view, view_confidence
from backend.sessions import get_session
from backend.views import page_context
from contracts.actions import AgentConfig
from contracts.events import AgentEvent

logger = logging.getLogger("agent.agent")

_PASS_THROUGH = {"confirm_request", "action", "verify", "evidence", "canvas"}


def llm_ready(llm) -> bool:
    """True when the provider can plausibly answer (a key is configured, or it is the test double)."""
    if llm is None:
        return False
    if isinstance(llm, FakeLLM):
        return True
    if isinstance(llm, ChainedLLMProvider):
        return llm_ready(llm.primary) or llm_ready(llm.fallback)
    return bool(getattr(llm, "api_key", ""))


def _skill_for(it: Interp):
    if it.intent == "view":
        return "view", run_view
    if it.intent == "data":
        return "data", run_data
    if it.intent in ("compare", "explain", "investigate"):
        return "reason", run_reason
    if it.intent == "act":
        return "act", run_act
    if it.intent == "lookup":
        return "lookup", run_lookup
    return "converse", run_converse


def ungrounded_numbers(text: str, query: str, it: Interp, out: SkillResult, session) -> list[str]:
    """Numbers in the final answer that have no source: not a fact, not in the evidence, not in the user's message
    or in the resolved periods. This is the end-to-end faithfulness measure (K4)."""
    from agent.narrator import _extract_digits, _extract_evidence_numbers

    allowed = set(_extract_evidence_numbers(session.evidence)) | _extract_digits(query) | {str(n) for n in range(0, 31)}
    for a in it.assumptions:
        allowed |= _extract_digits(a)
    for p in (it.primary, it.baseline):
        if p:
            allowed |= _extract_digits(p.label) | _extract_digits(p.start) | _extract_digits(p.end)
    if out.facts is not None:
        for t in out.facts.all_texts():
            allowed |= _extract_digits(t)
    for t in out.extra_text:
        allowed |= _extract_digits(str(t))
    return sorted(_extract_digits(text) - allowed)


def paraphrase(it: Interp, ctx: TurnContext) -> str:
    if it.paraphrase:
        return it.paraphrase
    m = ctx.lex.metrics[it.metric].title if it.metric in ctx.lex.metrics else None
    bits = {"view": "Open", "data": "Tell you", "compare": "Compare", "explain": "Explain the change in",
            "lookup": f"Show everything about {it.entity_id or 'that record'}",
            "investigate": "Check the health of the business", "act": "Start the action", "undo": "Undo the last change",
            "help": "Explain what I can do", "clarify": "Ask what you mean", "decline": "Decline this request"}
    s = bits.get(it.intent, "Handle")
    if m:
        s += f" {m.lower()}"
    if it.group_by:
        s += " by " + ", ".join(g.replace("_", " ") for g in it.group_by)
    if it.filters:
        s += " for " + "; ".join(f"{k.replace('_', ' ')} {', '.join(v)}" for k, v in it.filters.items())
    if it.primary:
        s += f" in {it.primary.label}"
    return s


class AppPilotAgent:
    """The AppPilot agent. `llm` may be None or unavailable: rules and deterministic composition still answer."""

    def __init__(self, llm: LLMProvider | None = None, strong_llm: LLMProvider | None = None, use_llm: bool = True):
        try:
            if not use_llm:
                self.llm, strong = None, None
            elif llm is not None:
                self.llm, strong = llm, strong_llm
            else:
                self.llm, strong = get_llm_pair()
        except Exception as e:  # noqa: BLE001  (bad LLM_PROVIDER must not stop the app)
            logger.warning("LLM unavailable (%s); running rules-only", e)
            self.llm, strong = None, None
        self.strong_llm = strong or self.llm

    async def handle_form_action(self, session_id: str, action: str, config: AgentConfig | None = None) -> AsyncIterator[AgentEvent]:
        """A button on the live form (submit / cancel): the interpretation is known, so no model call is needed."""
        mem = get_agent_memory(session_id)
        if mem.form is None:
            return
        forced = Interp(intent="act", form_action=action, write_api=mem.form.api_id, source="ui", confidence=1.0,
                        paraphrase=f"{action.capitalize()} the open form")
        async for e in self.handle(session_id, f"{action} the form", config, forced=forced):
            yield e

    async def handle(self, session_id: str, message: str, config: AgentConfig | None = None,
                     forced: Interp | None = None) -> AsyncIterator[AgentEvent]:
        t0 = time.time()
        cfg = config or AgentConfig()
        seq = 0

        def ev(kind: str, data: dict) -> AgentEvent:
            nonlocal seq
            seq += 1
            return AgentEvent(seq=seq, type=kind, data=data)  # type: ignore[arg-type]

        yield ev("understanding", {"query": message, "session_id": session_id})

        # ---------------------------------------------------------------- L0 context
        s = get_session(session_id)
        try:
            page_ctx = await page_context(s.state)
        except Exception as e:  # noqa: BLE001
            logger.warning("page context failed: %s", e)
            page_ctx = {"page": {"page_id": s.state.page_id}, "widgets": [], "filters": []}
        lex, cap, mem = get_lexicon(), get_capability_model(), get_agent_memory(session_id)
        remember_choice(mem, message)             # an answer to the question just asked is kept for the session
        use_llm = llm_ready(self.llm)
        ctx = TurnContext(session=s, text=message, page_ctx=page_ctx, cfg=cfg, lex=lex, cap=cap, mem=mem,
                          llm=self.llm if use_llm else None, as_of=lex.as_of)
        ctx.llm_strong = self.strong_llm if llm_ready(self.strong_llm) else ctx.llm  # type: ignore[attr-defined]

        # ---------------------------------------------------------------- L1-L2 perceive + interpret
        try:
            if forced is not None:
                ctx.ann = perceive(message, lex, lex.as_of)
                it = forced
            else:
                it = await interpret(ctx)
            if it.source == "fast" and it.intent == "view" and ctx.llm is not None:
                best, margin = view_confidence(ctx, it)
                if best is None or margin < 0.7 or best.score < 2.0:   # the rules are not sure which page: ask the model
                    f = await llm_interpret(ctx, ctx.ann)
                    if f is not None:
                        it = reconcile(f, ctx, ctx.ann, "llm")
        except Exception as e:  # noqa: BLE001
            logger.exception("interpretation failed")
            it = Interp(intent="clarify", source="rules",
                        clarification="I had trouble understanding that. Could you rephrase it, for example with a metric, a period and where to look?")
            yield ev("error", {"code": "interpret_failed", "message": f"{type(e).__name__}: {e}", "recoverable": True})

        # part of the request outside what can be mapped, or a value that could mean different fields with materially different
        # answers, is dealt with before anything runs
        if forced is None:
            if guard_unmapped(ctx, it):
                yield ev("analysis_step", {"title": "Checking what I understood", "detail": "part of the request is outside what I can map"})
            elif await ambiguity_clarification(ctx, it):
                yield ev("analysis_step", {"title": "Checking what each reading would give", "detail": "the answer differs, so I will ask"})

        # an open form is parked (not lost) when the user moves to another topic
        park_note = ""
        if mem.form is not None and not mem.form.parked and it.intent not in ("act", "help", "undo", "clarify", "decline", "lookup"):
            mem.form.parked = True
            park_note = f"\n\n*Your “{mem.form.title}” draft is parked; say “continue the form” to resume it.*"

        yield ev("understood", {"paraphrase": paraphrase(it, ctx), "assumptions": it.assumptions, "intent": it.intent,
                                "source": it.source, "confidence": it.confidence,
                                "parser": ctx.ann.summary() if ctx.ann is not None else {}})
        yield ev("frame", {"intent": it.intent, "shape": it.shape, "metric": it.metric, "group_by": it.group_by,
                           "filters": it.filters, "period": it.primary.label if it.primary else None,
                           "baseline": it.baseline.label if it.baseline else None, "compare_kind": it.compare_kind,
                           "slices": [{"label": x.label, "filters": x.filters} for x in it.slices],
                           "target_page": it.target_page, "write_api": it.write_api, "follow_up": it.follow_up})

        # ---------------------------------------------------------------- L3 route + L4 skill
        name, runner = _skill_for(it)
        yield ev("plan", {"intent": it.intent, "skill": name, "steps": [{"tool": name, "args": {"metric": it.metric}}],
                          "clarifying_question": it.clarification})
        out = SkillResult()
        cursor = len(s.events)

        def drain() -> list:
            nonlocal cursor
            fresh = [e for e in s.events[cursor:] if e.type in _PASS_THROUGH]
            cursor = len(s.events)
            return fresh

        try:
            async for kind, data in runner(ctx, it, out):
                for e in drain():
                    yield ev(e.type, e.data)
                yield ev(kind, data)
        except Exception as e:  # noqa: BLE001
            logger.exception("skill %s failed", name)
            out.text = f"Something went wrong while handling that ({type(e).__name__}). I did not change anything I could not verify."
            out.status = "FAILED"
            yield ev("error", {"code": "skill_failed", "message": f"{type(e).__name__}: {e}", "recoverable": True})
        for e in drain():
            yield ev(e.type, e.data)

        # ---------------------------------------------------------------- L5 status + L6 answer
        status = out.status if out.status in ("VERIFIED", "PARTIAL", "FAILED") else "INFO"
        if it.ignored and status == "VERIFIED":      # something in the request was dropped: never claim full verification
            status = "PARTIAL"
        yield ev("status", {"status": status, "verified": status == "VERIFIED", "mismatches": out.mismatches,
                            "screen": out.screen_note, "skill": name})

        text = (out.text or "Done.").strip()
        if out.screen_note:
            text += f"\n\n{out.screen_note}"
        shown = [a for a in it.assumptions if not a.startswith(("Period:", "Compared with:"))] if name in ("view", "data", "reason") else []
        resolved = [a for a in it.assumptions if a.startswith(("Period:", "Compared with:"))] if name in ("view", "data", "reason") else []
        extra = (resolved + shown)[:5]
        if extra and status != "INFO":
            text += "\n\n*Assumptions: " + " ".join(extra) + "*"
        text += park_note
        if status == "FAILED" and not text.startswith(("⚠", "Something went wrong")):
            text = "⚠ " + text
        ungrounded = ungrounded_numbers(text, message, it, out, s)
        yield ev("answer", {"text": text, "citations": out.citations, "deep_links": out.deep_links,
                            "suggestions": out.suggestions, "status": status, "assumptions": it.assumptions,
                            "ungrounded_numbers": ungrounded, "grounded": not ungrounded, "intent": it.intent,
                            "narrative_used": out.narrative_used, "claims": out.claims})

        # ---------------------------------------------------------------- memory
        mem.turns += 1
        mem.last_answer = text
        if it.intent in ("view", "data", "compare", "explain", "investigate") and status != "FAILED":
            mem.last = it
        if out.screen_note or name == "view":
            mem.last_action_kind = mem.last_action_kind if name == "act" else "state"
        yield ev("done", {"steps": out.steps, "replans": 0, "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
                          "intent": it.intent, "skill": name, "interpretation": it.source})


_DEFAULT_AGENT = AppPilotAgent()


async def handle(session_id: str, message: str, config: AgentConfig | None = None) -> AsyncIterator[AgentEvent]:
    """Module-level entrypoint (kept for routes/session.py and scripts)."""
    async for e in _DEFAULT_AGENT.handle(session_id, message, config):
        yield e
