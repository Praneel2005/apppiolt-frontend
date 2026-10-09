"""Streaming events from the agent to the chat UI (Contract 5).

The agent yields these as it works so the UI can fill the action log immediately
(perceived latency) instead of waiting for the final answer.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AgentEvent(BaseModel):
    model_config = {"extra": "forbid"}

    seq: int
    type: Literal[
        "understanding",    # data: {intent, slots}
        "retrieval",        # data: {candidates: [{page_id, score}]}
        "plan",             # data: {steps: [...]}
        "validation",       # data: {ok, issues}
        "confirm_request",  # data: {action_id, summary}  -> UI shows confirm card
        "action",           # data: {tool, args, status}
        "verify",           # data: {ok, mismatches}
        "evidence",         # data: Evidence
        "canvas",           # data: CanvasSpec (generated chart / table / form to show in the main area)
        "answer_delta",     # data: {text}
        "answer",           # data: {text, citations: [evidence_id], deep_links: [route]}
        "error",            # data: {code, message, recoverable}
        "done",             # data: {steps, replans, elapsed_ms}
    ]
    data: dict = {}


class AgentEventEnvelope(BaseModel):
    """How an AgentEvent travels over the session WebSocket."""

    model_config = {"extra": "forbid"}
    type: Literal["agent_event"] = "agent_event"
    event: AgentEvent
