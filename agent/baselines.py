"""Baselines for AppPilot evaluation:
- B0: LLM only (no metadata cards, no tool schema grounding)
- B1: Prompt stuffing (all page cards in context, no validator, no verifier)
- B2: RAG + Tools without gates (retrieval + tools, no validator, no verifier, no replan)
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from agent.context import get_memory
from agent.llm import LLMProvider, get_llm
from agent.narrator import narrate
from agent.planner import get_default_catalog, plan_request
from agent.retrieval import RetrievalCandidate, RetrievalResult
from backend import executor
from backend.config import app_model
from backend.sessions import get_session
from backend.views import page_context
from contracts.actions import AgentConfig, Intent, Plan, ToolCall
from contracts.events import AgentEvent

logger = logging.getLogger("agent.baselines")


class BaselineB0Agent:
    """B0: LLM only, no metadata.
    Simulates a generic LLM agent without domain metadata cards or application schema.
    """

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm or get_llm()

    async def handle(
        self,
        session_id: str,
        message: str,
        config: AgentConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        t0 = time.time()
        cfg = config or AgentConfig(use_validator=False, use_verifier=False, max_replans=0)
        s = get_session(session_id)

        seq = 0

        def _ev(ev_type: str, data: dict) -> AgentEvent:
            nonlocal seq
            seq += 1
            return AgentEvent(seq=seq, type=ev_type, data=data)  # type: ignore

        yield _ev("understanding", {"query": message, "session_id": session_id})
        # Empty retrieval
        yield _ev("retrieval", {"pages": [], "apis": [], "expanded": []})

        empty_retrieval = RetrievalResult(query=message, pages=[], apis=[], graph_expanded_ids=[])

        plan, _ = await plan_request(
            query=message,
            page_ctx={"page": {"page_id": s.state.page_id}},
            memory=get_memory(session_id),
            llm=self.llm,
            config=cfg,
            catalog={},
            override_retrieval=empty_retrieval,
        )

        yield _ev(
            "plan",
            {
                "intent": plan.intent.name,
                "steps": [st.model_dump() for st in plan.steps],
                "clarifying_question": plan.clarifying_question,
            },
        )

        if plan.intent.name in ("clarify_needed", "out_of_scope", "unsafe_action") or not plan.steps:
            reply_text = plan.clarifying_question or "I am unable to perform that action."
            yield _ev("answer", {"text": reply_text, "citations": [], "deep_links": []})
            yield _ev("done", {"steps": 0, "replans": 0, "elapsed_ms": round((time.time() - t0) * 1000.0, 1)})
            return

        # Attempt to run without validator/verifier
        events_start = len(s.events)
        await executor.run_plan(s, plan, cfg, auto_confirm=True)
        for ev in s.events[events_start:]:
            if ev.type in ("confirm_request", "action", "verify", "evidence", "canvas"):
                yield _ev(ev.type, ev.data)

        text, citations, links = await narrate(plan, s.evidence, message, s.state, False, cfg)
        yield _ev("answer", {"text": text, "citations": citations, "deep_links": links})
        yield _ev(
            "done",
            {
                "steps": len(plan.steps),
                "replans": 0,
                "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
            },
        )


class BaselineB1Agent:
    """B1: Prompt stuffing.
    Dumps all 115 page cards into prompt context, single navigate/set-state call,
    no validator, no verifier.
    """

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm or get_llm()

    async def handle(
        self,
        session_id: str,
        message: str,
        config: AgentConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        t0 = time.time()
        cfg = config or AgentConfig(use_validator=False, use_verifier=False, max_replans=0)
        s = get_session(session_id)
        catalog = get_default_catalog()
        app = app_model()

        seq = 0

        def _ev(ev_type: str, data: dict) -> AgentEvent:
            nonlocal seq
            seq += 1
            return AgentEvent(seq=seq, type=ev_type, data=data)  # type: ignore

        yield _ev("understanding", {"query": message, "session_id": session_id})

        # All pages stuffed into retrieval result
        all_pages = [
            RetrievalCandidate(
                id=p.page_id,
                kind="page",
                title=p.title,
                snippet=p.description or "",
                score=1.0,
            )
            for p in app.pages
        ]
        all_apis = [
            RetrievalCandidate(
                id=k,
                kind="api",
                title=e.get("summary", k),
                snippet=e.get("description", ""),
                score=1.0,
            )
            for k, e in catalog.items()
        ]
        stuffed_retrieval = RetrievalResult(query=message, pages=all_pages, apis=all_apis, graph_expanded_ids=[])

        yield _ev(
            "retrieval",
            {
                "pages": [{"id": p.id, "title": p.title, "score": 1.0} for p in all_pages[:10]],
                "apis": [{"id": a.id, "title": a.title, "score": 1.0} for a in all_apis[:10]],
                "expanded": [],
            },
        )

        page_ctx = await page_context(s.state)
        plan, _ = await plan_request(
            query=message,
            page_ctx=page_ctx,
            memory=get_memory(session_id),
            llm=self.llm,
            config=cfg,
            catalog=catalog,
            override_retrieval=stuffed_retrieval,
        )

        yield _ev(
            "plan",
            {
                "intent": plan.intent.name,
                "steps": [st.model_dump() for st in plan.steps],
                "clarifying_question": plan.clarifying_question,
            },
        )

        if plan.intent.name in ("clarify_needed", "out_of_scope", "unsafe_action") or not plan.steps:
            reply_text = plan.clarifying_question or "I am unable to perform that action."
            yield _ev("answer", {"text": reply_text, "citations": [], "deep_links": []})
            yield _ev("done", {"steps": 0, "replans": 0, "elapsed_ms": round((time.time() - t0) * 1000.0, 1)})
            return

        events_start = len(s.events)
        await executor.run_plan(s, plan, cfg, auto_confirm=True)
        for ev in s.events[events_start:]:
            if ev.type in ("confirm_request", "action", "verify", "evidence", "canvas"):
                yield _ev(ev.type, ev.data)

        text, citations, links = await narrate(plan, s.evidence, message, s.state, False, cfg)
        yield _ev("answer", {"text": text, "citations": citations, "deep_links": links})
        yield _ev(
            "done",
            {
                "steps": len(plan.steps),
                "replans": 0,
                "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
            },
        )


class BaselineB2Agent:
    """B2: RAG + Tools without gates.
    Uses normal retrieval and tools, but disables validator, verifier, and bounded replan.
    """

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm or get_llm()

    async def handle(
        self,
        session_id: str,
        message: str,
        config: AgentConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        t0 = time.time()
        # Force gating switches off
        cfg = config or AgentConfig()
        cfg.use_validator = False
        cfg.use_verifier = False
        cfg.max_replans = 0
        cfg.use_citation_check = False

        s = get_session(session_id)
        catalog = get_default_catalog()

        seq = 0

        def _ev(ev_type: str, data: dict) -> AgentEvent:
            nonlocal seq
            seq += 1
            return AgentEvent(seq=seq, type=ev_type, data=data)  # type: ignore

        yield _ev("understanding", {"query": message, "session_id": session_id})

        page_ctx = await page_context(s.state)
        plan, retrieval = await plan_request(
            query=message,
            page_ctx=page_ctx,
            memory=get_memory(session_id),
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

        if plan.intent.name in ("clarify_needed", "out_of_scope", "unsafe_action") or not plan.steps:
            reply_text = plan.clarifying_question or "I am unable to perform that action."
            yield _ev("answer", {"text": reply_text, "citations": [], "deep_links": []})
            yield _ev("done", {"steps": 0, "replans": 0, "elapsed_ms": round((time.time() - t0) * 1000.0, 1)})
            return

        events_start = len(s.events)
        await executor.run_plan(s, plan, cfg, auto_confirm=True)
        for ev in s.events[events_start:]:
            if ev.type in ("confirm_request", "action", "verify", "evidence", "canvas"):
                yield _ev(ev.type, ev.data)

        text, citations, links = await narrate(plan, s.evidence, message, s.state, False, cfg)
        yield _ev("answer", {"text": text, "citations": citations, "deep_links": links})
        yield _ev(
            "done",
            {
                "steps": len(plan.steps),
                "replans": 0,
                "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
            },
        )
