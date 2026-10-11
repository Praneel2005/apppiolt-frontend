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
    "search_apis",
    "navigate",
    "set_filter",
    "clear_filter",
    "set_date_range",
    "set_sort",
    "read_view",
    "run_metric_query",
    "compare_periods",
    "explain_change",
    "analyze_trend",  # deterministic monthly trend statistics (slope, peak, MoM, YoY) for a metric
    "open_deep_link",
    "call_api",       # read a catalogue API (orders.search, tickets.search, ...)
    "write_api",      # change data through a catalogue write API: ALWAYS preview + user confirmation
    "render_canvas",  # show a generated chart / table / form when no page fits
]

# side effect classes drive the confirmation policy. "write" is confirmed even when confirm_mode is off.
SIDE_EFFECT: dict[str, Literal["none", "ui_state", "write", "canvas"]] = {
    "search_pages": "none",
    "search_apis": "none",
    "navigate": "ui_state",
    "set_filter": "ui_state",
    "clear_filter": "ui_state",
    "set_date_range": "ui_state",
    "set_sort": "ui_state",
    "read_view": "none",
    "run_metric_query": "none",
    "compare_periods": "none",
    "explain_change": "none",
    "analyze_trend": "none",
    "open_deep_link": "ui_state",
    "call_api": "none",
    "write_api": "write",
    "render_canvas": "canvas",
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
        "empty_result", "timeout", "denied", "api_error", "none",
    ] = "none"
    message: str = ""
    evidence_id: str | None = None  # key into the evidence store for analytics results


class Evidence(_Model):
    """Every number shown to the user must map to one of these (provenance)."""

    evidence_id: str
    kind: Literal["widget_read", "metric_query", "comparison", "contribution", "trend", "api_read", "api_write", "search"]
    source: str  # widget_id or metric id
    query: str | None = None  # the validated SQL that produced it
    values: dict  # machine-readable numbers, e.g. {"current": 1.8e6, "previous": 1.99e6, "delta_pct": -7.4}


class AgentConfig(_Model):
    confirm_mode: bool = False  # True: confirm every state change; False: auto-run with undo
    max_plan_retries: int = 2
    max_replans: int = 2
    max_tool_retries: int = 1
    # V3 agent: writes are NEVER approved on the user's behalf unless this is set (tests / evaluation only)
    auto_confirm_writes: bool = False
    # V3 agent: after answering a data question, open the page that shows the same numbers (when one exists exactly)
    open_supporting_view: bool = True
    # V3 agent: let the LLM write an interpretation on top of the deterministic analysis
    use_synthesis: bool = True
    # V3 agent: skip the LLM for clear navigation commands (faster, but keyword-based). Off by default: the model decides.
    use_fast_lane: bool = False
    # V3 agent: ask before answering when a value could mean different fields and the numbers differ materially. The live API turns
    # this on for every message unless the request sets it; library and test defaults keep it off.
    clarify_ambiguity: bool = False
    # V3 agent: do not present a confident answer to a request that was only partly understood (unknown words or values, a period
    # outside the data); same default policy as clarify_ambiguity.
    guard_unmapped: bool = False
    # V3 agent: repair misspelt words in the parser (ablation switch; on by default)
    repair_typos: bool = True
    # ablation switches (evaluation turns these off one at a time)
    use_validator: bool = True
    use_verifier: bool = True
    use_graph_expansion: bool = True
    use_citation_check: bool = True
