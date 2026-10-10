"""Form contract for Act skill (Contract 8 in AGENT_V2_DESIGN.md).

Maintains interactive draft forms filled by chat, validated server-side,
with preview, confirmation, submission, and undo.
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class FormState(BaseModel):
    model_config = {"extra": "forbid"}

    form_id: str
    api_id: str
    title: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)  # field -> user|agent|default
    errors: dict[str, str] = Field(default_factory=dict)   # field -> error message
    missing: list[str] = Field(default_factory=list)       # required fields still missing
    status: Literal["draft", "previewing", "confirming", "submitted", "cancelled"] = "draft"
    parked: bool = False
    action_id: str | None = None
