"""Interfaces between tracks (Contract 6).

Shashank codes the agent against these Protocols using fakes; Shreeniketh implements them.
Nobody should import the other track's concrete classes.
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol

from .actions import AgentConfig, Evidence, Plan, StepResult, ToolCall
from .events import AgentEvent
from .metadata import Application
from .query import QueryResult, QuerySpec, ValidationResult, VerifyResult
from .ui_state import RenderAck, UiState


class Validator(Protocol):
    def validate(self, app: Application, plan: Plan, state: UiState | None) -> ValidationResult: ...


class QueryEngine(Protocol):
    def run(self, spec: QuerySpec) -> QueryResult: ...


class Executor(Protocol):
    async def run_step(self, session_id: str, step: ToolCall) -> StepResult:
        """Apply a validated step. UI-state tools push ApplyState and wait for the RenderAck."""
        ...

    async def last_ack(self, session_id: str) -> RenderAck | None: ...
    def current_state(self, session_id: str) -> UiState | None: ...
    def evidence(self, session_id: str, evidence_id: str) -> Evidence | None: ...


class Verifier(Protocol):
    def verify(self, app: Application, expected: UiState, ack: RenderAck) -> VerifyResult: ...


class Agent(Protocol):
    """Implemented by the agent package; called by the backend for every user message."""

    def handle(self, session_id: str, message: str, config: AgentConfig) -> AsyncIterator[AgentEvent]: ...
