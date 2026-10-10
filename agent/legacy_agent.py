"""Baseline B3: the previous LLM tool-plan agent (retrieval -> one planner call -> validator -> executor -> narrator).

Kept unchanged in behaviour for the evaluation ablations ("what does the layered V3 pipeline add over a tool-plan agent?").
The production agent is `agent.agent.AppPilotAgent`.
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from agent.context import ConversationTurn, get_memory
from agent.llm import LLMProvider, get_llm
from agent.narrator import narrate
from agent.planner import get_default_catalog, plan_request
from backend import executor
from backend.sessions import get_session
from backend.views import page_context
from contracts.actions import AgentConfig
from contracts.events import AgentEvent

logger = logging.getLogger("agent.legacy")


class LegacyToolPlanAgent:
    """Tool-plan agent (baseline B3)."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm or get_llm()

    async def handle(self, session_id: str, message: str, config: AgentConfig | None = None) -> AsyncIterator[AgentEvent]:
        t0 = time.time()
        cfg = config or AgentConfig()
        seq = 0

        def _ev(ev_type: str, data: dict) -> AgentEvent:
            nonlocal seq
            seq += 1
            return AgentEvent(seq=seq, type=ev_type, data=data)  # type: ignore

        yield _ev("understanding", {"query": message, "session_id": session_id})
        s = get_session(session_id)
        memory = get_memory(session_id)
        page_ctx = await page_context(s.state)
        catalog = get_default_catalog()

        plan, retrieval = await plan_request(query=message, page_ctx=page_ctx, memory=memory, llm=self.llm, config=cfg,
                                             catalog=catalog)
        yield _ev("retrieval", {"pages": [{"id": p.id, "title": p.title, "score": p.score} for p in retrieval.pages],
                                "apis": [{"id": a.id, "title": a.title, "score": a.score} for a in retrieval.apis],
                                "expanded": retrieval.graph_expanded_ids})
        yield _ev("plan", {"intent": plan.intent.name, "steps": [st.model_dump() for st in plan.steps],
                           "clarifying_question": plan.clarifying_question})

        if plan.intent.name in ("clarify_needed", "out_of_scope", "unsafe_action") or not plan.steps:
            reply_text = plan.clarifying_question or "I am unable to perform that action."
            yield _ev("answer", {"text": reply_text, "citations": [], "deep_links": []})
            yield _ev("done", {"steps": 0, "replans": 0, "elapsed_ms": round((time.time() - t0) * 1000.0, 1)})
            memory.add_turn(ConversationTurn(user_message=message, intent=plan.intent.name, plan_summary=reply_text))
            return

        val_res = getattr(retrieval, "validation", None)
        is_val_ok = val_res.ok if val_res else True
        yield _ev("validation", {"ok": is_val_ok, "issues": [x.model_dump() for x in val_res.issues] if val_res else []})
        if cfg.use_validator and not is_val_ok:
            reply_text = "I cannot execute that plan because it violates application constraints: " + \
                         "; ".join(i.message for i in val_res.issues[:2])
            yield _ev("answer", {"text": reply_text, "citations": [], "deep_links": []})
            yield _ev("done", {"steps": 0, "replans": 0, "elapsed_ms": round((time.time() - t0) * 1000.0, 1)})
            return

        events_start = len(s.events)
        exec_res = await executor.run_plan(s, plan, cfg, auto_confirm=cfg.auto_confirm_writes)
        exec_ok = exec_res.get("ok", False)
        for ev in s.events[events_start:]:
            if ev.type in ("confirm_request", "action", "verify", "evidence", "canvas"):
                yield _ev(ev.type, ev.data)

        screen_verified = s.last_verify.ok if s.last_verify else True
        verified = screen_verified and exec_ok
        status_tag = "VERIFIED" if verified else ("FAILED" if not exec_ok and not screen_verified else "PARTIAL")
        yield _ev("status", {"status": status_tag, "verified": verified, "screen_verified": screen_verified,
                             "exec_ok": exec_ok, "mismatches": s.last_verify.mismatches if s.last_verify else []})
        answer_text, citations, deep_links = await narrate(plan=plan, evidence_map=s.evidence, query=message,
                                                           final_state=s.state, verified=verified, status=status_tag,
                                                           mismatches=s.last_verify.mismatches if s.last_verify else [],
                                                           assumptions=[], config=cfg)
        yield _ev("answer", {"text": answer_text, "citations": citations, "deep_links": deep_links})
        memory.add_turn(ConversationTurn(user_message=message, intent=plan.intent.name, plan_summary=answer_text,
                                         evidence_ids=citations, slots=plan.intent.slots))
        yield _ev("done", {"steps": len(plan.steps), "replans": 0, "elapsed_ms": round((time.time() - t0) * 1000.0, 1)})
