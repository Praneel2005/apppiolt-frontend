"""Tool / plan contract (Contract 3).

The LLM emits a Plan made of ToolCalls. Argument *values* that refer to the app
(page ids, filter ids, values, metrics) are validated against the Application metadata
before anything executes. Tool argument schemas are generated per tenant from metadata
(see agent/tools.py, to be written), so the enums below are the *shape*, not the values.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .ui_state import DateRange, UiState


class _Model(BaseModel):
    model_config = {"extra": "forbid"}


ToolName = Literal[
    "search_pages",
    "navigate",
    "set_filter",
    "set_date_range",
    "set_sort",
    "read_view",
    "run_metric_query",
    "compare_periods",
    "explain_change",
    "open_deep_link",
]

# side effect classes drive the confirmation policy
SIDE_EFFECT: dict[str, Literal["none", "ui_state"]] = {
    "search_pages": "none",
    "navigate": "ui_state",
    "set_filter": "ui_state",
    "set_date_range": "ui_state",
    "set_sort": "ui_state",
    "read_view": "none",
    "run_metric_query": "none",
    "compare_periods": "none",
    "explain_change": "none",
    "open_deep_link": "ui_state",
}


class ToolCall(_Model):
    tool: ToolName
    args: dict
    # post-condition the verifier will check after execution, e.g.
    # {"route": "/sales/revenue-by-region"} or {"filters": {"region": ["West"]}}
    expect: dict = {}


class Intent(_Model):
    name: Literal[
        "navigate",
        "set_state",
        "read_view",
        "analyze_trend",
        "compare_periods",
        "explain_change",
        "multi_step",
        "clarify_needed",
        "out_of_scope",
        "unsafe_action",
    ]
    slots: dict = {}  # metric, dimension, period, filters ... (free-form, validated later)
    ambiguity: str | None = None  # if set, the agent asks this clarifying question


class Plan(_Model):
    """One structured-output call returns intent AND plan (single LLM round trip)."""

    intent: Intent
    steps: list[ToolCall] = []
    clarifying_question: str | None = None
    # analytic exploration may be re-planned after observations (bounded, default max 2)
    allow_replan: bool = False


class StepResult(_Model):
    step_index: int
    ok: bool
    error_code: Literal[
        "invalid_reference", "invalid_value", "state_mismatch", "query_failed",
        "empty_result", "timeout", "denied", "none",
    ] = "none"
    message: str = ""
    evidence_id: str | None = None  # key into the evidence store for analytics results


class Evidence(_Model):
    """Every number shown to the user must map to one of these (provenance)."""

    evidence_id: str
    kind: Literal["widget_read", "metric_query", "comparison", "contribution"]
    source: str  # widget_id or metric id
    query: str | None = None  # the validated SQL that produced it
    values: dict  # machine-readable numbers, e.g. {"current": 1.8e6, "previous": 1.99e6, "delta_pct": -7.4}


class AgentConfig(_Model):
    confirm_mode: bool = False  # True: confirm every state change; False: auto-run with undo
    max_plan_retries: int = 2
    max_replans: int = 2
    max_tool_retries: int = 1
    # ablation switches (evaluation turns these off one at a time)
    use_validator: bool = True
    use_verifier: bool = True
    use_graph_expansion: bool = True
    use_citation_check: bool = True
