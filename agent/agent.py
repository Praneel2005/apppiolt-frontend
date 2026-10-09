"""Master Agent Loop for AppPilot: orchestrates planning, validation, execution, verification, and narration.

Interface:
    async def handle(session_id: str, message: str, config: AgentConfig | None = None) -> AsyncIterator[AgentEvent]

Yields real-time AgentEvents:
    understanding -> retrieval -> plan -> validation -> confirm_request -> action -> verify -> evidence -> canvas -> answer -> done
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from agent.context import ConversationTurn, get_memory
from agent.llm import LLMProvider, get_llm
from agent.narrator import narrate
from agent.planner import get_default_catalog, plan_request
from backend import executor, sessions
from backend.sessions import get_session
from backend.views import page_context
from contracts.actions import AgentConfig, Plan
from contracts.events import AgentEvent

logger = logging.getLogger("agent.agent")


class AppPilotAgent:
    """AppPilot Operations Agent instance."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm or get_llm()

    async def handle(
        self,
        session_id: str,
        message: str,
        config: AgentConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        t0 = time.time()
        cfg = config or AgentConfig()
        seq = 0

        def _ev(ev_type: str, data: dict) -> AgentEvent:
            nonlocal seq
            seq += 1
            return AgentEvent(seq=seq, type=ev_type, data=data)  # type: ignore

        # 1. Understanding event
        yield _ev("understanding", {"query": message, "session_id": session_id})

        # 2. Ingest session state & memory
        s = get_session(session_id)
        memory = get_memory(session_id)
        page_ctx = await page_context(s.state)
        catalog = get_default_catalog()

        # 3. Planning (1 structured LLM call + validator repair loop)
        plan, retrieval = await plan_request(
            query=message,
            page_ctx=page_ctx,
            memory=memory,
            llm=self.llm,
            config=cfg,
            catalog=catalog,
        )

        yield _ev(
            "retrieval",
            {
                "pages": [{"id": p.id, "title": p.title, "score": p.score} for p in retrieval.pages],
                "apis": [{"id": a.id, "title": a.title, "score": a.score} for a in retrieval.apis],
                "expanded": retrieval.graph_expanded_ids,
            },
        )

        yield _ev(
            "plan",
            {
                "intent": plan.intent.name,
                "steps": [st.model_dump() for st in plan.steps],
                "clarifying_question": plan.clarifying_question,
            },
        )

        # 4. Clarification, Out-of-Scope, or Unsafe Refusal
        if plan.intent.name in ("clarify_needed", "out_of_scope", "unsafe_action") or not plan.steps:
            reply_text = plan.clarifying_question or "I am unable to perform that action."
            yield _ev("answer", {"text": reply_text, "citations": [], "deep_links": []})
            elapsed = (time.time() - t0) * 1000.0
            yield _ev("done", {"steps": 0, "replans": 0, "elapsed_ms": round(elapsed, 1)})

            memory.add_turn(
                ConversationTurn(
                    user_message=message,
                    intent=plan.intent.name,
                    plan_summary=reply_text,
                )
            )
            return

        yield _ev("validation", {"ok": True, "issues": []})

        # 5. Execution Pipeline
        events_start = len(s.events)
        total_steps = len(plan.steps)
        replan_count = 0

        # Run primary plan
        auto_confirm = not cfg.confirm_mode
        exec_res = await executor.run_plan(s, plan, cfg, auto_confirm=auto_confirm)

        # Stream new events created on the session by the executor
        for ev in s.events[events_start:]:
            if ev.type in ("confirm_request", "action", "verify", "evidence", "canvas"):
                yield _ev(ev.type, ev.data)

        # 6. Bounded Replan (if allowed and evidence gathered)
        if plan.allow_replan and cfg.max_replans > 0 and s.evidence:
            replan_count += 1
            # Update page context
            page_ctx = await page_context(s.state)
            ev_summary = f"Evidence gathered so far: {list(s.evidence.keys())}"
            follow_up_query = f"{message}\n(Follow-up with observations: {ev_summary})"

            replan, _ = await plan_request(
                query=follow_up_query,
                page_ctx=page_ctx,
                memory=memory,
                llm=self.llm,
                config=cfg,
                catalog=catalog,
            )

            # Filter out UI steps that already succeeded
            new_steps = [st for st in replan.steps if st.tool not in ("navigate", "set_filter", "set_date_range")]
            if new_steps:
                replan.steps = new_steps
                re_start = len(s.events)
                await executor.run_plan(s, replan, cfg, auto_confirm=auto_confirm)
                for ev in s.events[re_start:]:
                    if ev.type in ("action", "evidence", "canvas"):
                        yield _ev(ev.type, ev.data)
                total_steps += len(new_steps)

        # 7. Narration
        verified = s.last_verify.ok if s.last_verify else True
        answer_text, citations, deep_links = await narrate(
            plan=plan,
            evidence_map=s.evidence,
            query=message,
            final_state=s.state,
            verified=verified,
            config=cfg,
        )

        yield _ev("answer", {"text": answer_text, "citations": citations, "deep_links": deep_links})

        # 8. Update dialogue memory
        memory.add_turn(
            ConversationTurn(
                user_message=message,
                intent=plan.intent.name,
                plan_summary=answer_text,
                evidence_ids=citations,
                slots=plan.intent.slots,
            )
        )

        elapsed = (time.time() - t0) * 1000.0
        yield _ev(
            "done",
            {
                "steps": total_steps,
                "replans": replan_count,
                "elapsed_ms": round(elapsed, 1),
            },
        )


# Global default agent instance
_DEFAULT_AGENT = AppPilotAgent()


async def handle(
    session_id: str,
    message: str,
    config: AgentConfig | None = None,
) -> AsyncIterator[AgentEvent]:
    """Module-level entrypoint satisfying routes/session.py contract."""
    async for ev in _DEFAULT_AGENT.handle(session_id, message, config):
        yield ev
